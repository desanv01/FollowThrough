# Development-preview GitHub delivery

This record maps the reviewed source units to their scoped hosted PRs. Each listed head passed Ubuntu and Windows offline CI before merge; each merge was read back against the expected tree. The legacy [draft PR #1](https://github.com/desanv01/FollowThrough/pull/1) remains historical and unmerged.

The reviewed local source baseline was `5070bb4bc610b78d07e4c7011354e4640631bb80`, tree `49eea65ecc298c413788ed75eda387fb7bb2c17f`. The source replay ended at `0bc54fb81ad827527b5308e40b06855200a97775`, tree `3b1cb02566b18322855cd07f98ae53c5c0cd02ed`. Compared with that local baseline, the source replay changes only `tests/test_p2_protected.py`: two separately reviewed portability corrections. Product source bytes are preserved. Subsequent scoped PRs add release status documentation, independent analysis coverage to CI, and the source-package workflow.

Snapshot before this documentation record: `6eca17b7d0ba4c5b7292b80b6fb8f5ea0ffb10ed`, tree `87f8a3159ec848c3cee8df19caf9bc44125cf601`.

## Scoped source units

| Unit | PR | Checked head | Hosted CI | Verified merge |
|---|---|---|---|---|
| P6.1-CI | [#2](https://github.com/desanv01/FollowThrough/pull/2) | `dc447c708fee` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496451461) | `be41c8fdd9f7` |
| P6.2-BYTES | [#3](https://github.com/desanv01/FollowThrough/pull/3) | `665c1f987083` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496461246) | `72d502ce8c5d` |
| P3.6-BUDGET | [#4](https://github.com/desanv01/FollowThrough/pull/4) | `9861243626be` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496469669) | `8393d7fd21ad` |
| P4.1-REPORTING | [#5](https://github.com/desanv01/FollowThrough/pull/5) | `f86f3407d932` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496479221) | `1c7caaa60c3f` |
| P3.6.3-RECEIPT-LOOP | [#6](https://github.com/desanv01/FollowThrough/pull/6) | `badb21397814` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496489062) | `177246b2777c` |
| P3.6-PROVIDER | [#7](https://github.com/desanv01/FollowThrough/pull/7) | `67bca6ad395e` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496498372) | `653a85a5145e` |
| P1.3-SCENARIO | [#8](https://github.com/desanv01/FollowThrough/pull/8) | `441006f4b037` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496508215) | `901b7489d803` |
| P1.3.7.4-DRIVER | [#9](https://github.com/desanv01/FollowThrough/pull/9) | `2156d0c36972` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496525730) | `1bd03f5ad93f` |
| P2.3.4-ISOLATION | [#10](https://github.com/desanv01/FollowThrough/pull/10) | `fafd38ce9a14` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496535619) | `3086325b0297` |
| P2.3.5-EXECUTOR | [#11](https://github.com/desanv01/FollowThrough/pull/11) | `0270dcb880ba` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496571928) | `a9976c1846fe` |
| P4.1.6-TRAJECTORY | [#12](https://github.com/desanv01/FollowThrough/pull/12) | `def0a72fc6dc` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496622397) | `c23fe2aea2f2` |
| P4.5-LEDGER | [#13](https://github.com/desanv01/FollowThrough/pull/13) | `da6ba5cb30ce` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496672595) | `adc665f39cb9` |
| P5.3-ANALYSIS | [#40](https://github.com/desanv01/FollowThrough/pull/40) | `b85319bcef28` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496723093) | `41916281f32d` |
| P6.2-RELEASE-DOCS | [#41](https://github.com/desanv01/FollowThrough/pull/41) | `8183395da89e` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496772284) | `bc2c6dd2f337` |
| P0.3.2-OUTCOMES | [#42](https://github.com/desanv01/FollowThrough/pull/42) | `62faf7d21ef4` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496825889) | `927696f639aa` |
| P4.1-RUNTIME | [#43](https://github.com/desanv01/FollowThrough/pull/43) | `50115f6c731a` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496875051) | `8951944043e7` |
| P4.1.6-PROJECTION | [#44](https://github.com/desanv01/FollowThrough/pull/44) | `b06d7512fdf0` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496926833) | `d1c61d7fb5a7` |
| P4.5.4-PROGRESS | [#45](https://github.com/desanv01/FollowThrough/pull/45) | `2d1e402b5c8b` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42496977452) | `30c6977623f5` |
| P4.6-VIEWER | [#46](https://github.com/desanv01/FollowThrough/pull/46) | `5b12c33dbade` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42497030507) | `42d66803d6e9` |
| P1.4.3-EXAMPLES | [#47](https://github.com/desanv01/FollowThrough/pull/47) | `586f1cc4d2e1` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42497078670) | `c02dda4f3888` |
| P2.4.1-RETRY | [#48](https://github.com/desanv01/FollowThrough/pull/48) | `c0649ba0fa10` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498361064) | `c30ad00587c2` |
| P2.4.2-CACHE | [#49](https://github.com/desanv01/FollowThrough/pull/49) | `09df540f36cc` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498370013) | `535b4b40f425` |
| P3.6.4-LIVE-GUARD | [#50](https://github.com/desanv01/FollowThrough/pull/50) | `1dec782e8d58` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498373834) | `be1c9059d917` |
| P5-TOOLING | [#51](https://github.com/desanv01/FollowThrough/pull/51) | `1edf010e10f7` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498376843) | `4a70018b6b8c` |
| P5-REPRO | [#52](https://github.com/desanv01/FollowThrough/pull/52) | `2f236993dace` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498380791) | `8bcbca6aeb30` |
| P4.1.1-IDENTITY | [#53](https://github.com/desanv01/FollowThrough/pull/53) | `266748a3891b` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498385251) | `f918e9a5e02c` |
| P2-NATIVE-FIX | [#54](https://github.com/desanv01/FollowThrough/pull/54) | `c31cdbaba644` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498902934) | `f78d39a08c4d` |
| P4.1.1-HARDENING | [#55](https://github.com/desanv01/FollowThrough/pull/55) | `6040a4080e45` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498910881) | `20f308276341` |
| P2-NATIVE-WITNESS | [#56](https://github.com/desanv01/FollowThrough/pull/56) | `1362624b3892` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498915179) | `ac6bc2a592aa` |
| P6.2.3-LICENCE | [#57](https://github.com/desanv01/FollowThrough/pull/57) | `8d3cfeb1beab` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498919993) | `2104636fdabb` |
| P4.1.2-SIX | [#60](https://github.com/desanv01/FollowThrough/pull/60) | `ed0e15586625` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498924382) | `afd64b4f3995` |
| P2-CACHE-V2-FIX | [#61](https://github.com/desanv01/FollowThrough/pull/61) | `b5d558a9a95e` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498928737) | `538ede2f58b4` |
| P6.2.3-LICENCE-FIX | [#62](https://github.com/desanv01/FollowThrough/pull/62) | `dc3a664367f5` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498932765) | `6f857603e3de` |
| P5-RECEIPT | [#63](https://github.com/desanv01/FollowThrough/pull/63) | `a3f09cc3ac64` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498937280) | `8fc94d288232` |
| P3.6.4-LIVE-MODE | [#64](https://github.com/desanv01/FollowThrough/pull/64) | `4121085c49c6` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498941401) | `1cddd01aa4ba` |
| P3-INFRA-CLASSIFICATION | [#65](https://github.com/desanv01/FollowThrough/pull/65) | `bd2e37b36162` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498945489) | `bcf4bc32093d` |
| P3-P4-LIVE-CHECK | [#66](https://github.com/desanv01/FollowThrough/pull/66) | `844e14aea7c9` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42498950377) | `0bc54fb81ad8` |

## Hosted portability corrections

- [PR #58](https://github.com/desanv01/FollowThrough/pull/58): P2 native receipt portability. Merged into its scoped dependency branch, then carried forward by normal merges.
- [PR #67](https://github.com/desanv01/FollowThrough/pull/67): P2: make mount witness fixture portable on Ubuntu. Merged into its scoped dependency branch, then carried forward by normal merges.

Native-receipt-dependent tests require explicit `FOLLOWTHROUGH_NATIVE_RECEIPT` opt-in. Hosted CI records a concrete skip when native evidence is unavailable; synthetic contract tests remain enabled. The mount-fixture correction keeps all six witness assertions and adds no skip. Hosted checks do not claim Docker or paid provider qualification.

## Release documentation and delivery workflow

- [P6-STATUS-DOCS (#69)](https://github.com/desanv01/FollowThrough/pull/69), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499125362), merge `da7ad2174cf3`.
- [P6-CI-P5-COVERAGE (#70)](https://github.com/desanv01/FollowThrough/pull/70), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499134217), merge `d0263e7b7318`.
- [P6-SOURCE-PACKAGE (#71)](https://github.com/desanv01/FollowThrough/pull/71), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499142485), merge `c08ec4a9b05a`.
- [P1-DELIVERY-STATUS (#72)](https://github.com/desanv01/FollowThrough/pull/72), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499150992), merge `447c1ebb4455`.
- [P2-DELIVERY-STATUS (#73)](https://github.com/desanv01/FollowThrough/pull/73), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499159570), merge `b2822aeac04f`.
- [P3-DELIVERY-STATUS (#74)](https://github.com/desanv01/FollowThrough/pull/74), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499167576), merge `f94ac62589ca`.
- [P4-DELIVERY-STATUS (#75)](https://github.com/desanv01/FollowThrough/pull/75), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499176016), merge `945f5f9ab00b`.
- [P5-DELIVERY-STATUS (#76)](https://github.com/desanv01/FollowThrough/pull/76), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499185234), merge `58b5d8e8bdf7`.
- [P6-DELIVERY-STATUS (#77)](https://github.com/desanv01/FollowThrough/pull/77), [hosted CI](https://github.com/desanv01/FollowThrough/actions/runs/42499194895), merge `6eca17b7d0ba`.

## Runtime and remaining scope

The one recorded real DeepSeek development attempt received a passing protected Retry v1 functional grade and an overall infrastructure error after a public-check setup failure. It is excluded from scoring. The source correction was checked with provider-disabled fixtures; no second paid attempt was made for this delivery. See [development status](DEVELOPMENT-STATUS.md) for the recorded usage, reservations, unknowns and open reconciliation items. Pilot and formal results remain pending.

| Phase | Delivered preview scope | Remaining scope |
|---|---|---|
| P1 | Authored tasks, graders, scenarios, examples | Full live role visibility and evaluated checkpoint qualification |
| P2 | Bounded execution source and authored native controls | Original Docker setup diagnosis and broader runtime qualification |
| P3 | Provider/budget/role-loop source and provenance correction | Successful qualified complete live workflow |
| P4 | Reporting, trajectory tools, CLI ledger and viewer | Live reporting reconciliation, pilots and freeze |
| P5 | Independent analysis, receipt validator and reproduction tools | Formal dataset and analysis |
| P6 | Scoped GitHub source delivery, hosted CI and source-package workflow | Video discussion/production and owner-controlled submission |

The manual source-package workflow archives only tracked source and emits commit, tree and SHA-256 files. Source artifacts are the delivery target for this CLI repository; no running web service is deployed. Credentials, private coordination, raw runtime traces, budget databases and local Git bundles are excluded.

Video production is held until the owner discusses it after GitHub delivery. Upload and final submission are separate actions.

## 9 November qualification and T3 deliveries

Each scoped unit below passed hosted Ubuntu/Windows CI on its PR head and has a verified merge/tree readback. The real execution and remaining study limits are described in the dated development record.

| PR | Source head | Hosted CI | Merge |
|---|---|---|---|
| [#79](https://github.com/desanv01/FollowThrough/pull/79) | `9026f9e5805f` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42545416845) | `89f9b5444042` |
| [#80](https://github.com/desanv01/FollowThrough/pull/80) | `c36373f91a41` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42545666444) | `1c21e532da21` |
| [#81](https://github.com/desanv01/FollowThrough/pull/81) | `7a082dfda4d5` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42545922284) | `8fc77f9141a9` |
| [#82](https://github.com/desanv01/FollowThrough/pull/82) | `73aa9640537a` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42546441907) | `30ac75f48fe1` |
| [#83](https://github.com/desanv01/FollowThrough/pull/83) | `8fdb226c34e4` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42546994987) | `a42c3d6d52eb` |
| [#86](https://github.com/desanv01/FollowThrough/pull/86) | `355fa37f18e9` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42548194658) | `f33b2dae943f` |
| [#87](https://github.com/desanv01/FollowThrough/pull/87) | `b6b21c183185` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42548887259) | `48790313665d` |
| [#88](https://github.com/desanv01/FollowThrough/pull/88) | `60842774761b` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42550647849) | `113943a7eef9` |
| [#89](https://github.com/desanv01/FollowThrough/pull/89) | `f59eccda117c` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42551845353) | `a2328e3fb241` |
| [#90](https://github.com/desanv01/FollowThrough/pull/90) | `3b3d050cfb3a` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42552839415) | `c6a5cfeead9a` |
| [#91](https://github.com/desanv01/FollowThrough/pull/91) | `d46861d3b5be` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42553604014) | `beed38f2a40d` |
| [#92](https://github.com/desanv01/FollowThrough/pull/92) | `fd6add748162` | [pass](https://github.com/desanv01/FollowThrough/actions/runs/42556952739) | `29dcef206fa6` |

The earlier one-attempt and video-hold statements above are historical delivery scope. Current retained evidence is three excluded real attempts; evaluations are paused and local video production has been authorized. Public video upload and final submission await owner review. Legacy draft PR #1 remains preserved.
