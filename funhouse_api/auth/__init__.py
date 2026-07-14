"""Authentication package for the FunHouse Container API (Spec 2).

Contains the self-managed :mod:`~funhouse_api.auth.service` (bcrypt password
hashing + PyJWT HS256 token lifecycle), the public ``POST /auth/login`` router
(:mod:`~funhouse_api.auth.router`), and the ``require_auth`` Token_Verifier
dependency (:mod:`~funhouse_api.auth.dependencies`). No external identity
provider is contacted; everything runs in-process inside the container
(Req 13.3).
"""
