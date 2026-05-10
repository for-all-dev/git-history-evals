# Experiment summary

Total runs: **986**

## mode = `baseline`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 136 | 2 | 0.0147 | 4.5143 | 108.6029 | 0.7231 | 247882.0 | 0.7760 | 142.3625 | 36.0000 | 0.7760 | 1.6855 | 36.0000 | 1.5137 | 1.9296 | 2.9262 |
| 3 | 170 | 1 | 0.0059 | 4.3854 | 103.8647 | 0.2743 | 178225.0 | 1.0000 | 96.4310 | — | 1.0000 | 0.6163 | — | 0.8699 | 0.8843 | 1.0537 |
| 5 | 170 | 3 | 0.0176 | 6.9041 | 144.4118 | 0.6159 | 308642.0 | 0.9987 | 110.1210 | — | 0.9987 | 1.1326 | — | 1.0527 | 0.8533 | 1.6838 |
| 7 | 170 | 2 | 0.0118 | 6.8767 | 152.9471 | 0.6620 | 249663.0 | 0.9999 | 114.5005 | — | 0.9999 | 1.4973 | — | 2.4137 | 0.7876 | 2.2377 |
| 10 | 170 | 3 | 0.0176 | 7.2142 | 141.4529 | 0.6986 | 308642.0 | 0.9987 | 110.2757 | — | 0.9987 | 1.3249 | — | 0.8209 | 0.7631 | 1.8263 |
| 15 | 170 | 3 | 0.0176 | 6.9209 | 191.7235 | 0.7427 | 308642.0 | 0.9987 | 85.6060 | — | 0.9987 | 1.2931 | — | 0.8686 | 0.7184 | 1.5475 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.4581**

## baseline vs agent

_No shared deletion sizes between baseline and agent runs._

## drift faithfulness (Pearson r vs deletion_size)

| metric | baseline |
|---|---|
| mean_compile_time_ratio | -0.0117 |
| mean_n_assumptions_diff | — |
| mean_normalized_edit_distance | 0.3651 |
| mean_proof_chars_ratio | -0.2601 |
| mean_proof_lines_ratio | -0.7470 |
| mean_tactic_count_ratio | -0.4191 |
| mean_vo_bytes_ratio | 0.6558 |
| pass_rate | 0.4581 |
