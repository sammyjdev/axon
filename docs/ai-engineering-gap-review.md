# AXON AI Engineering Gap Review

This review uses the study map lens to inspect AXON as an AI engineering
system. The goal is to find the smallest conceptual upgrades that would make
the project easier to explain, easier to debug, and easier to defend in a
portfolio.

## What AXON already does well

- Separates engine code from vault data.
- Routes by task type and profile instead of by a single default model.
- Treats risk as a first-class concern through tool classes and consent gates.
- Uses reversible compression instead of only destructive summarization.
- Records telemetry and validation stats.
- Integrates GLYPH as a dedicated retrieval layer rather than folding graph logic into the core.

## Concept gaps worth addressing

| Concept | Current state | Gap | Why it matters |
| --- | --- | --- | --- |
| Failure taxonomy | Implicit across docs and decisions | No single vocabulary for routing, retrieval, memory, tool, and governance failures | Faster diagnosis and clearer postmortems |
| Scorecard | Telemetry exists | No compact cross-profile scorecard for cost, latency, and success rate | Harder to compare profiles and regressions |
| Stop policy | Present in gates and denied tools | Not surfaced as a first-class design rule | Makes fallback and refusal logic easier to audit |
| Memory quality | Strong retrieval primitives | No one-page summary of when recall helps and when it becomes noise | Important for explaining why AXON is useful |
| Lesson memory | ADRs and notes exist | No study index that ties lessons to concrete artifacts | Makes the architecture easier to teach and reuse |

## Improvements that seem worth considering

1. Add a single AXON concept index.
   The index should say which file proves each concept and which project
   capability it belongs to.

2. Add a failure taxonomy section to the docs.
   Every bad run should be named the same way across routing, retrieval, and
   governance discussions.

3. Add a compact scorecard section.
   Include task type, model profile, latency, cost, and notable failure mode.

4. Make stop criteria explicit.
   Fallback and denial should read like policy, not like incidental behavior.

5. Add a memory quality note.
   Make it clear that compression is valuable only when recall restores the
   right context later.

## What not to change

- Do not collapse AXON, GLYPH, and rtkx into one monolith.
- Do not replace the existing gates with a more abstract scoring layer.
- Do not add a heavy evaluation framework before the taxonomy is clearer.

## Most likely next move

The next useful improvement is probably a small cross-linking index that
connects:

- concept
- controlling artifact
- measurement artifact
- known failure mode

That would make AXON easier to study and easier to use as portfolio evidence.
