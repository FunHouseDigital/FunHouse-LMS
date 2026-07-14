"""Integration test for the Bedrock Batch submission path (Task 4.3).

Exercises the full BedrockBatchProvider flow against a **moto-backed S3** and a
**stubbed Bedrock client** (no live AWS): input JSONL is written to S3, the job
is submitted via CreateModelInvocationJob, polled to a terminal state, and the
JSONL batch-output is parsed into normalized ``LLMResult`` items.

This is an example-based integration test (1-3 examples), not a property test,
matching the design's Testing Strategy.
"""

from __future__ import annotations

import json

import boto3
import pytest
from moto import mock_aws

from funhouse_pipeline.llm import BedrockBatchProvider, LLMResult, LLMResultItem

_REGION = "af-south-1"
_BUCKET = "funhouse-archive-test"


def _make_bucket(s3) -> None:
    s3.create_bucket(
        Bucket=_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": _REGION},
    )


class _StubBedrock:
    """Minimal stand-in for the boto3 ``bedrock`` client.

    On job submission it reads the input JSONL from S3 (so the test can assert
    the provider wrote it), simulates the model by writing an output JSONL under
    the job's output prefix, and reports the job Completed -- exactly the shape
    the provider polls for.
    """

    def __init__(self, s3, bucket: str) -> None:
        self._s3 = s3
        self._bucket = bucket
        self.create_calls: list[dict] = []
        self.get_calls: list[str] = []
        self.captured_input_records: list[dict] = []
        self._status_by_job: dict[str, str] = {}

    def _key_from_uri(self, uri: str) -> str:
        prefix = f"s3://{self._bucket}/"
        assert uri.startswith(prefix), f"unexpected S3 URI {uri!r}"
        return uri[len(prefix):]

    def create_model_invocation_job(self, **kwargs):
        self.create_calls.append(kwargs)

        input_key = self._key_from_uri(kwargs["inputDataConfig"]["s3InputDataConfig"]["s3Uri"])
        output_prefix = self._key_from_uri(kwargs["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"])

        # Read what the provider uploaded (asserts input JSONL exists in S3).
        body = self._s3.get_object(Bucket=self._bucket, Key=input_key)["Body"].read().decode("utf-8")

        out_lines: list[str] = []
        for line in body.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            self.captured_input_records.append(record)
            record_id = record["recordId"]
            # Simulate a Claude Messages response body.
            model_output = {
                "content": [{"type": "text", "text": f"extracted::{record_id}"}],
                "stop_reason": "end_turn",
            }
            out_lines.append(json.dumps({"recordId": record_id, "modelOutput": model_output}))

        out_key = output_prefix + "input.jsonl.out"
        self._s3.put_object(
            Bucket=self._bucket,
            Key=out_key,
            Body=("\n".join(out_lines) + "\n").encode("utf-8"),
        )

        job_arn = "arn:aws:bedrock:af-south-1:123456789012:model-invocation-job/job-abc123"
        self._status_by_job[job_arn] = "Completed"
        return {"jobArn": job_arn}

    def get_model_invocation_job(self, jobIdentifier):
        self.get_calls.append(jobIdentifier)
        return {"status": self._status_by_job[jobIdentifier], "jobArn": jobIdentifier}


@mock_aws
def test_bedrock_batch_submission_writes_input_submits_and_parses_output():
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)
    stub_bedrock = _StubBedrock(s3, _BUCKET)

    provider = BedrockBatchProvider(
        s3_bucket=_BUCKET,
        role_arn="arn:aws:iam::123456789012:role/bedrock-batch",
        s3_client=s3,
        bedrock_client=stub_bedrock,
        region=_REGION,
        poll_interval_seconds=0,  # keep the test instant
    )

    context = {
        "system_prompt": "Pricing tiers: R10/20min. Schools: Mofulatshepe. Players: Thabo.",
        "records": [
            {"custom_id": "card-1", "text": "membership card 1"},
            {"custom_id": "card-2", "text": "membership card 2"},
        ],
    }

    result = provider.generate("extract_records", context)

    # --- Job was submitted exactly once, and polled at least once. ---
    assert len(stub_bedrock.create_calls) == 1
    assert len(stub_bedrock.get_calls) >= 1
    submit = stub_bedrock.create_calls[0]
    assert submit["roleArn"] == "arn:aws:iam::123456789012:role/bedrock-batch"
    assert submit["inputDataConfig"]["s3InputDataConfig"]["s3Uri"].startswith(f"s3://{_BUCKET}/")
    assert submit["outputDataConfig"]["s3OutputDataConfig"]["s3Uri"].startswith(f"s3://{_BUCKET}/")

    # --- Input JSONL was written to S3 with the business-rules system prompt. ---
    assert len(stub_bedrock.captured_input_records) == 2
    first_input = stub_bedrock.captured_input_records[0]
    assert first_input["recordId"] == "card-1"
    assert first_input["modelInput"]["system"] == context["system_prompt"]
    assert first_input["modelInput"]["messages"][0]["role"] == "user"

    # The input object still exists in S3 after the run (provenance kept).
    input_key = submit["inputDataConfig"]["s3InputDataConfig"]["s3Uri"].split(f"s3://{_BUCKET}/")[1]
    stored = s3.get_object(Bucket=_BUCKET, Key=input_key)["Body"].read().decode("utf-8")
    assert "card-1" in stored and "card-2" in stored

    # --- Output JSONL was parsed into normalized LLMResults. ---
    assert isinstance(result, LLMResult)
    assert result.provider == "bedrock"
    assert len(result.items) == 2
    assert all(isinstance(i, LLMResultItem) for i in result.items)
    by_id = {i.custom_id: i for i in result.items}
    assert by_id["card-1"].content == "extracted::card-1"
    assert by_id["card-2"].content == "extracted::card-2"
    assert by_id["card-1"].stop_reason == "end_turn"
    assert result.metadata["job_arn"].endswith("job-abc123")


@mock_aws
def test_bedrock_batch_raises_on_failed_job():
    s3 = boto3.client("s3", region_name=_REGION)
    _make_bucket(s3)

    class _FailingBedrock(_StubBedrock):
        def create_model_invocation_job(self, **kwargs):
            self.create_calls.append(kwargs)
            job_arn = "arn:aws:bedrock:af-south-1:123456789012:model-invocation-job/job-fail"
            self._status_by_job[job_arn] = "Failed"
            return {"jobArn": job_arn}

    stub = _FailingBedrock(s3, _BUCKET)
    provider = BedrockBatchProvider(
        s3_bucket=_BUCKET,
        s3_client=s3,
        bedrock_client=stub,
        region=_REGION,
        poll_interval_seconds=0,
    )

    from funhouse_pipeline.llm import BedrockBatchError

    with pytest.raises(BedrockBatchError):
        provider.generate("extract_records", {"records": [{"custom_id": "x", "text": "t"}]})
