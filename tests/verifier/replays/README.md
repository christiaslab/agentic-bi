# Verifier replays

Every file here is **written by hand** from the verdict schema (`src/agentic_bi/verifier/verdict.py`) and the v1 prompt. None is model output, and each says so in its `provenance` (`hand_written`) and its `note`. They prove the verifier's plumbing offline: the request reaches the judge, the judge's text is parsed against the schema, each issue code survives the round trip, and a malformed answer is an error. They do not prove that a model would give these verdicts; that is the live run of S5b.

Each replay names its question by `question_id` in `evals/questions.yaml`, and a test keeps the question text equal to the set's. The query is either the question's expected query (a consistent verdict) or a deliberately wrong one (an issue code, or a refusal). The file format is described in `src/agentic_bi/verifier/replay.py`.
