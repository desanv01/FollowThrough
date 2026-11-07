# Official DeepSeek adapter notes

Verified official documentation 7 November 2026. No request sent.

- https://api-docs.deepseek.com/quick_start/pricing/ maps `deepseek-flash` to DeepSeek-V4.1-Flash-1017. Record requested and returned identities; alias is not immutable snapshot assurance. Official base https://api.deepseek.com.
- https://api-docs.deepseek.com/guides/thinking_mode/ documents thinking enabled with explicit high effort. Tool-bearing subsequent requests retain returned assistant reasoning_content. Do not strip protocol fields or silently set ignored temperature controls.
- https://api-docs.deepseek.com/guides/tool_calls/ requires caller execution and matching tool_call_id results. Chat Completions does not support fabricated inserted tool calls; task fixture history must be labeled ordinary context, not forged assistant tool history.

Initial development uses pure protocol/fake transport checks. Actual API requests require explicit operation budget and environment-only DEEPSEEK_API_KEY loaded outside evaluated sandbox. Main checked only presence: absent in current process. No keys were read from other apps.

Conservative planning rates (USD per million tokens, checked date): cache-hit input 0.006; uncached input 0.30; output 1.20. These are published peak Flash rates; use conservative bounds, retain raw usage, and verify pricing before campaign. Off-peak rates are lower. No search/fetch calls planned.

Do not publish raw provider reasoning by default; retain required protocol content internally for correct continuation, and separate sanitized visible traces from restricted transport capture. Unknown usage fields remain null, not zero.
