"""AWS Bedrock **Batch** provider for the LLM abstraction (Req 4.1).

The historical backlog is a bulk, latency-insensitive workload, so extraction
uses Bedrock's asynchronous Batch inference (~half the real-time cost). The flow
mirrors the design's Bedrock Batch sequence diagram:

1. Build a JSONL batch-input file (one line per source record) and write it to S3.
2. Submit the job with ``CreateModelInvocationJob``.
3. Poll ``GetModelInvocationJob`` until the job reaches a terminal state.
4. Read the JSONL batch-output from S3 and normalize it into an
   :class:`~funhouse_pipeline.llm.base.LLMResult`.

Only S3 and Bedrock are touched. No banned service (Pinpoint, DynamoDB,
Cognito, Lambda-as-architecture) is referenced anywhere (Req 6.3), and the only
datastore remains PostgreSQL (Req 6.4).

Both the Bedrock and S3 clients are **injectable** so tests can drive the whole
path with a stubbed Bedrock client and a moto-backed S3 client -- no live AWS
calls (design: Testing Strategy / Integration tests).
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any, Mapping

from funhouse_pipeline.llm.base import (
    LLMResult,
    LLMResultItem,
    extract_records_from_context,
)

# Terminal job states reported by GetModelInvocationJob.
_TERMINAL_SUCCESS = "Completed"
_TERMINAL_FAILURE_STATES = frozenset({"Failed", "Stopped", "Expired", "PartiallyCompleted"})

# Default Claude model on Bedrock; overridable via context["model_id"] or ctor.
DEFAULT_MODEL_ID = "anthropic.claude-3-5-sonnet-20240620-v1:0"
_ANTHROPIC_VERSION = "bedrock-2023-05-31"


class BedrockBatchError(RuntimeError):
    """Raised when a Bedrock Batch job ends in a non-successful terminal state."""


class BedrockBatchProvider:
    """LLM provider backed by the Bedrock Batch (async model-invocation) API.

    Args:
        s3_bucket: Bucket used for batch input/output JSONL.
        role_arn: IAM role Bedrock assumes to read input / write output.
        s3_client: Injectable boto3 S3 client (moto in tests). Lazily created
            from ``region`` when omitted.
        bedrock_client: Injectable boto3 ``bedrock`` client (stub in tests).
            Lazily created from ``region`` when omitted.
        region: AWS region for lazily-created clients (default ``af-south-1``).
        model_id: Default model id when the context does not specify one.
        input_prefix / output_prefix: S3 key prefixes for batch I/O.
        poll_interval_seconds: Delay between ``GetModelInvocationJob`` polls
            (set to 0 in tests).
        max_poll_seconds: Upper bound on total polling before giving up.
        sleep: Injectable sleep function (defaults to ``time.sleep``).
    """

    name = "bedrock"

    def __init__(
        self,
        *,
        s3_bucket: str,
        role_arn: str | None = None,
        s3_client: Any | None = None,
        bedrock_client: Any | None = None,
        region: str = "af-south-1",
        model_id: str = DEFAULT_MODEL_ID,
        input_prefix: str = "bedrock-batch/input",
        output_prefix: str = "bedrock-batch/output",
        poll_interval_seconds: float = 30.0,
        max_poll_seconds: float = 24 * 60 * 60,
        sleep: Any | None = None,
    ) -> None:
        if not s3_bucket:
            raise ValueError("BedrockBatchProvider requires an s3_bucket for batch I/O")
        self._s3_bucket = s3_bucket
        self._role_arn = role_arn
        self._s3 = s3_client
        self._bedrock = bedrock_client
        self._region = region
        self._model_id = model_id
        self._input_prefix = input_prefix.strip("/")
        self._output_prefix = output_prefix.strip("/")
        self._poll_interval = poll_interval_seconds
        self._max_poll_seconds = max_poll_seconds
        self._sleep = sleep or time.sleep

    # ------------------------------------------------------------------ #
    # Lazy client construction (only when a real client was not injected)
    # ------------------------------------------------------------------ #
    @property
    def s3(self) -> Any:
        if self._s3 is None:
            import boto3

            self._s3 = boto3.client("s3", region_name=self._region)
        return self._s3

    @property
    def bedrock(self) -> Any:
        if self._bedrock is None:
            import boto3

            self._bedrock = boto3.client("bedrock", region_name=self._region)
        return self._bedrock

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #
    def generate(self, task: str, context: Mapping[str, Any]) -> LLMResult:
        """Run ``task`` over ``context`` via a Bedrock Batch job."""
        records = extract_records_from_context(context)
        system_prompt = context.get("system_prompt", "")
        model_id = context.get("model_id") or self._model_id

        job_name = f"{task}-{uuid.uuid4().hex[:12]}"
        input_key = f"{self._input_prefix}/{job_name}/input.jsonl"
        output_key_prefix = f"{self._output_prefix}/{job_name}/"

        # 1. Build + upload the JSONL batch input.
        record_ids = self._write_batch_input(records, system_prompt, model_id, input_key)

        # 2. Submit the job.
        job_arn = self._submit_job(job_name, model_id, input_key, output_key_prefix)

        # 3. Poll to a terminal state.
        self._await_terminal(job_arn)

        # 4. Read + normalize the JSONL batch output.
        items = self._read_batch_output(output_key_prefix, record_ids)

        return LLMResult(
            task=task,
            provider=self.name,
            items=tuple(items),
            model_id=model_id,
            metadata={"job_arn": job_arn, "job_name": job_name},
        )

    # ------------------------------------------------------------------ #
    # Step helpers
    # ------------------------------------------------------------------ #
    def _write_batch_input(
        self,
        records: Any,
        system_prompt: str,
        model_id: str,
        input_key: str,
    ) -> list[str]:
        """Serialize records to Bedrock Batch JSONL and upload to S3.

        Returns the ordered list of record ids so output can be correlated even
        if the provider returns lines out of order.
        """
        lines: list[str] = []
        record_ids: list[str] = []
        for index, record in enumerate(records):
            record_id = str(record.get("custom_id") or f"record-{index}")
            record_ids.append(record_id)
            model_input = _build_model_input(record, system_prompt)
            lines.append(json.dumps({"recordId": record_id, "modelInput": model_input}))

        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        self.s3.put_object(Bucket=self._s3_bucket, Key=input_key, Body=body)
        return record_ids

    def _submit_job(
        self,
        job_name: str,
        model_id: str,
        input_key: str,
        output_key_prefix: str,
    ) -> str:
        """Submit CreateModelInvocationJob and return the job ARN."""
        kwargs: dict[str, Any] = {
            "jobName": job_name,
            "modelId": model_id,
            "inputDataConfig": {
                "s3InputDataConfig": {
                    "s3Uri": f"s3://{self._s3_bucket}/{input_key}",
                }
            },
            "outputDataConfig": {
                "s3OutputDataConfig": {
                    "s3Uri": f"s3://{self._s3_bucket}/{output_key_prefix}",
                }
            },
        }
        if self._role_arn:
            kwargs["roleArn"] = self._role_arn

        response = self.bedrock.create_model_invocation_job(**kwargs)
        job_arn = response.get("jobArn")
        if not job_arn:
            raise BedrockBatchError("CreateModelInvocationJob returned no jobArn")
        return job_arn

    def _await_terminal(self, job_arn: str) -> str:
        """Poll GetModelInvocationJob until a terminal state is reached."""
        waited = 0.0
        while True:
            response = self.bedrock.get_model_invocation_job(jobIdentifier=job_arn)
            status = response.get("status")
            if status == _TERMINAL_SUCCESS:
                return status
            if status in _TERMINAL_FAILURE_STATES:
                message = response.get("message", "")
                raise BedrockBatchError(
                    f"Bedrock Batch job {job_arn} ended in state {status!r}: {message}"
                )
            if waited >= self._max_poll_seconds:
                raise BedrockBatchError(
                    f"Bedrock Batch job {job_arn} did not reach a terminal state "
                    f"within {self._max_poll_seconds}s (last status {status!r})"
                )
            self._sleep(self._poll_interval)
            waited += self._poll_interval

    def _read_batch_output(self, output_key_prefix: str, record_ids: list[str]) -> list[LLMResultItem]:
        """Read every ``.out`` JSONL object under the job's output prefix.

        Bedrock writes output under ``<outputS3>/<jobId>/<input>.jsonl.out``.
        Rather than reconstruct the exact key, we list objects under the job's
        output prefix and parse every ``.out`` file, which is robust to path
        variation across accounts/regions.
        """
        keys = self._list_output_keys(output_key_prefix)
        items_by_id: dict[str, LLMResultItem] = {}

        for key in keys:
            obj = self.s3.get_object(Bucket=self._s3_bucket, Key=key)
            body = obj["Body"].read()
            if isinstance(body, bytes):
                body = body.decode("utf-8")
            for line in body.splitlines():
                line = line.strip()
                if not line:
                    continue
                parsed = json.loads(line)
                record_id = str(parsed.get("recordId") or parsed.get("record_id") or "")
                model_output = parsed.get("modelOutput") or parsed.get("model_output") or {}
                content, stop_reason = _parse_model_output(model_output)
                items_by_id[record_id] = LLMResultItem(
                    custom_id=record_id,
                    content=content,
                    stop_reason=stop_reason,
                    raw=parsed,
                )

        # Preserve input order; include only ids we submitted (defensive).
        ordered: list[LLMResultItem] = []
        for record_id in record_ids:
            if record_id in items_by_id:
                ordered.append(items_by_id.pop(record_id))
        # Append any extra outputs not matched to a submitted id (should be rare).
        ordered.extend(items_by_id.values())
        return ordered

    def _list_output_keys(self, output_key_prefix: str) -> list[str]:
        """List ``.out`` object keys under the job output prefix via S3."""
        keys: list[str] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self._s3_bucket, "Prefix": output_key_prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = self.s3.list_objects_v2(**kwargs)
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if key.endswith(".out") or key.endswith(".jsonl.out"):
                    keys.append(key)
            if response.get("IsTruncated"):
                token = response.get("NextContinuationToken")
            else:
                break
        return sorted(keys)


# --------------------------------------------------------------------------- #
# Payload helpers (Claude Messages body shape on Bedrock)
# --------------------------------------------------------------------------- #


def _build_model_input(record: Mapping[str, Any], system_prompt: str) -> dict[str, Any]:
    """Build a Claude Messages ``modelInput`` body for one record.

    The business-rules ``system_prompt`` is supplied as the system field (Req
    4.2). Text and images from the record become a single user message.
    """
    content_blocks: list[dict[str, Any]] = []

    text = record.get("text")
    if text:
        content_blocks.append({"type": "text", "text": str(text)})

    for image in record.get("images", []) or []:
        # Images arrive as {"media_type": "image/png", "data": "<base64>"}.
        content_blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.get("media_type", "image/png"),
                    "data": image.get("data", ""),
                },
            }
        )

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    body: dict[str, Any] = {
        "anthropic_version": _ANTHROPIC_VERSION,
        "max_tokens": int(record.get("max_tokens", 4096)),
        "messages": [{"role": "user", "content": content_blocks}],
    }
    if system_prompt:
        body["system"] = system_prompt
    return body


def _parse_model_output(model_output: Mapping[str, Any]) -> tuple[str, str | None]:
    """Normalize a Claude Messages ``modelOutput`` body to (text, stop_reason).

    Handles the common shapes defensively so a malformed payload yields the raw
    JSON as text rather than dropping the record.
    """
    if not isinstance(model_output, Mapping):
        return json.dumps(model_output), None

    stop_reason = model_output.get("stop_reason")
    content = model_output.get("content")

    if isinstance(content, list):
        texts = [
            block.get("text", "")
            for block in content
            if isinstance(block, Mapping) and block.get("type") == "text"
        ]
        if texts:
            return "".join(texts), stop_reason

    if isinstance(content, str):
        return content, stop_reason

    # Some model families return a top-level "completion" or "output_text".
    for key in ("completion", "output_text", "text"):
        value = model_output.get(key)
        if isinstance(value, str):
            return value, stop_reason

    return json.dumps(model_output), stop_reason
