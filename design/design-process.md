# blue-data-web-source — Design Process

## How this design was developed

blue-data-web-source is a component of the Blue Data Intelligence platform. Its design follows the same process used across all blue-data source packages.

## Phases

### 1. Scenario definition
New features begin with a concrete scenario in `02-scenarios.md`. A scenario names a real problem (e.g. "re-embedding 500 pages when 480 haven't changed"), the data involved, and the expected outcome. No design work starts without a grounding scenario.

### 2. Architecture decision
Significant design choices are recorded in `13-decision-log.md` as ADR entries before implementation begins. The ADR format is: Date, Status, Decision, Rationale, Consequences. A decision is "Accepted" once it has been reviewed; "Superseded" if a later decision replaces it.

### 3. Document-first iteration
Architecture and interface changes are made in the design docs before the code. `03-high-level-architecture.md` and `04-detailed-architecture.md` describe the current state of the system, not the aspirational state — they are updated when implementation diverges.

### 4. Implementation
Implementation follows the design. The pipeline is a strict one-way chain; new stages are added by extending the relevant ABC and wiring through `WebPipeline`. No stage may import from a downstream stage.

### 5. Test first
Every new feature requires a unit test before the PR is merged. Integration tests (no network — HTTP mocked via pytest-httpx) are added for any change that touches the extraction or chunking layer. See `11-testing-strategy.md`.

## How to propose a change

1. Open a GitHub issue describing the motivation and the scenario it addresses.
2. Add or update the relevant design doc(s).
3. Record a new ADR entry in `13-decision-log.md` if the change affects public API, data model, or a cross-cutting architectural constraint.
4. Implement, test, and update `07-repo-organization.md` if new files are added.

## Document ownership

Design documents are owned by the team, not individuals. Any contributor may edit them. The source of truth for runtime behaviour is always the code; design docs that conflict with the code are wrong and should be corrected.
