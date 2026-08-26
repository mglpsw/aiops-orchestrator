# Router receipt-v2 fixtures

These fixtures are acceptance material for the `#200-C-WIRE` qualification
round. They are not claimed to be the historical R1-R28/M1-M12 repository
matrix; that provenance remains `UNOBSERVED`.

The grammar and route invariants are derived from
`mglpsw/agent-router-api@80e921dfc28436bd4fed8a4e1fa72ffaa168d10c`, especially
`app/agent_router/inference_receipt.py`. Tests replace only the request-bound
hashes and six caller declarations with values derived from the exact mocked
HTTP exchange. The local and provider-fallback route observations remain
fixture-owned and are validated without a live Router or provider call.
