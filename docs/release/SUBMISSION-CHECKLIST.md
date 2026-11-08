# Submission checklist

**Preparation status — 7 November 2026.** Drafted for release packet P6-RELEASE-PREP/r1 against frozen baseline commit `166a304b32d92f66519b8b4074fee510cbffdaeb`. This is a checklist for a **future, explicitly authorised** submission. Nothing here was uploaded, published or submitted, and no consent or eligibility is inferred. The deadline, size, video and field rules mentioned below are **historical planning inputs that have not been reverified**; they must be checked against the current official rules before any action. Main owns the final decision; this document does not grant permission.

## 1. Authorization and non-actions

- Submission, upload and publication require an explicit user/Main authorisation after the release gates and rules recheck. Preparation of this checklist is not that authorisation.
- No package, archive, video or repository release is created here. No network, browser, provider, credential or Git mutation was performed for this packet.
- If the official deadline has passed, or eligibility/experience requirements cannot be met, do **not** submit; report the condition to Main and preserve the work as a candidate.
- CI configuration is a later sequential P6 assignment. Do not wait for CI to fabricate a green badge, and do not claim CI has run.

## 2. Package contents to assemble

| Item | Status at preparation | Notes |
|---|---|---|
| Root `README.md` | Exists at baseline | Must remain at archive root if the historical root-README rule is reconfirmed |
| Public source tree | Exists at baseline `166a304` | Include only the reviewed commit/package tree, not unrelated dirty work |
| Task specifications and authored fixtures | Exist for retry and preview-cache | Include `tasks/`, `references/`, `grader/` and `tests/` as reviewed |
| Trusted runner source | Exists | `runner/` plus `Cargo.lock`; note controller supports retry only |
| DeepSeek adapter and offline tests | Exists | No live call, no provider credential, no billing claim |
| Methods, acceptance, contracts, limitations and contribution record | Methods/acceptance/contracts exist; the limitations, reproduction, demo, submission and contribution documents are **proposed co-deliveries** | Main reviews and integrates before package freeze |
| Scenario, isolation-plan and budget candidate material | Delivered but **unaccepted** | Not part of the public baseline; do not present as integrated capability |
| Frozen scenario/prompt/model/settings/tool versions | **Missing** | Required by the release gate; campaign not frozen |
| Protected grading and formal results | **Missing** | No results exist; do not include synthetic examples as results |
| Sanitised traces and attempt dispositions | **Missing** | Must preserve all attempts if a campaign is later run |
| Reproduction instructions | Drafted here; current commands in README/runner/adapter guides | Main verifies commands after qualification |
| Demo video and transcript | **Missing** | Not recorded; ≤5-minute limit historical and to be rechecked |
| Licence file | **Missing** | Selection pending; no grant asserted |
| CI workflow/evidence | **Missing** | Later sequential P6 assignment, out of this scope |
| Submission description/fields | **Missing** | Depends on official form and eligibility; do not draft claims beyond evidence |

## 3. Reproduction, source and video accessibility

- **Reproduction:** a new contributor must be able to identify the exact commit, Python/Rust prerequisites and trusted local-control commands, and must understand that these demonstrate checker behaviour rather than a model result. Link the reproduction checklist and state the current limits explicitly.
- **Source:** provide the public repository/commit and any archive with a verifiable hash. The GitHub baseline is published at `166a304`; later accepted changes need their own identity. Do not describe the working tree as the baseline.
- **Video:** if a video is submitted, it must be accessible — captions and/or a transcript, clear narration, sufficient contrast, and no information conveyed by colour alone. The historical ≤5-minute limit must be rechecked. The demo outline is not a recording.
- **Claims:** every result shown must carry its evidence label (`real_local_control`, synthetic/planned, or formal result), and the video/description must distinguish fixture controls from model behaviour. No fabricated end-to-end footage.

## 4. Privacy and security exclusions

Exclude from any public package, archive, video or form:

- `.env.local` and any credential store, API key, token or secret value;
- `.coordination/` and private packet, review, candidate or delivery material;
- raw provider traces, hidden reasoning, unbounded stdout/stderr and private conversation content;
- personal data, unrelated local files, absolute private paths and machine-specific temporary paths;
- `runs/`, `tmp/`, scratch directories and any unredacted execution artefacts;
- development instructions or private workflow files inside evaluated workspaces, and any grader/reference solution exposed to an evaluated agent.

Before packaging, search the staged file list for secrets and confirm `.gitignore` covers local credentials. If a secret has ever been committed, do not simply delete it from the latest tree; escalate to Main for history handling and rotation. This checklist does not perform a secret scan.

## 5. Licence and attribution decisions

- **Licence choice is pending.** No open-source licence is selected or asserted, and this document does not choose one. A licence decision is the user's/Main's, ideally after checking the official rules and third-party obligations.
- Preserve the contribution record: user direction and motivating experience; Main coordination/integration/acceptance/publication; Codex Sol Medium implementation and cache takeover; Sol High independent reviews; human-launched DeepSeek workers for cache, scenario, isolation-plan and budget candidates. Worker `COMPLETE` is not integration or acceptance.
- Do not invent authors, professional experience, affiliations, endorsements, reviewer outcomes, commits or third-party grants.
- Confirm no third-party code, data, images or music are bundled without a recorded permission/licence. The pinned contribution ledger states that no third-party assets/data were copied into task fixtures; the release baseline forbids adding any through documentation. If a video adds music/fonts/images, record their licences separately.

## 6. Eligibility and personal fields — unknown

The official eligibility criteria, experience requirements, team/individual rules, age/region restrictions, consent language and required personal fields have **not** been verified. The earlier planning note flagged a possible experience requirement; it remains unresolved. Do not assert eligibility or complete personal/consent fields by inference.

| Field | Status |
|---|---|
| Eligibility (individual/team, region, age, experience) | **Unknown — recheck official rules** |
| Team name, entrant name, contact details | **Not decided / not collected** |
| Consent to rules, publicity or data processing | **Not obtained** |
| Experience/affiliation statements | **Unknown — must not be invented** |
| Tax/payment details (if any) | **Not applicable unless rules require and user approves** |

Main/user must complete these deliberately. If a required field is unavailable or the entrant is ineligible, stop rather than guess.

## 7. Official-rule recheck

The following were earlier planning inputs and are **historical, not newly verified**:

- deadline **9 November 2026, 13:15 Malaysia time** — historical; recheck the official source for the current deadline/time zone;
- archive **under 50 MB with `README.md` at the root** — historical; recheck the size limit and layout requirement;
- video **≤5 minutes** — historical; recheck duration and accessibility requirements;
- accessible **video, source and description fields** — historical; recheck exact fields, required links and permissions.

Before submitting, re-read the current official rules and record the checked date, source URL, and any differences. Do not rely on this document or on memory for a deadline. A rule change can invalidate the package layout, video length or eligibility.

## 8. Final authorized submit and receipt verification

Only after the user explicitly authorises submission and Main confirms the gates:

1. Freeze the package and compute its hash; confirm the archive contents and root layout against the rechecked rules.
2. Verify the video URL, source URL and description render correctly for a logged-out viewer, and that captions/transcript are present.
3. Confirm licence, attribution, privacy exclusions and eligibility choices are final.
4. Submit through the official channel. Do not use inferred consent, automation or a substitute entrant.
5. Capture the submission confirmation/receipt (submission ID, timestamp, package hash, entrant identity as shown) and store it with the release record. Verify the receipt matches the submitted package.
6. Report to Main: what was submitted, where, when, under which rules version, and the receipt identity. If confirmation is missing, treat the submission as unverified and escalate; do not resubmit blindly.

No upload, publication, form entry or submission occurs in this packet.

## 9. Open decision list for Main

| Decision/fact | Current state |
|---|---|
| Final commit/tag and archive name | Unknown; baseline `166a304` |
| Licence selection | Pending |
| Eligibility and entrant details | Unknown; recheck rules |
| Official deadline/limits | Historical; recheck |
| Accepted results and traces | None |
| Video/transcript | None |
| CI evidence | Later P6 assignment |
| Submission authorisation | Not given |
| Receipt/confirmation | None |
