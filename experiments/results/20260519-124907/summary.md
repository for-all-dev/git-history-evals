# Experiment summary

Total runs: **1082**

## mode = `baseline`

| deletion_size | n_total | n_pass | n_admitted | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 170 | 1 | 2 | 0.0059 | 14.1965 | 598.6353 | 0.6686 | 353594.0 | 0.7760 | 212.0947 | 36.0000 | 0.7760 | 1.8413 | 36.0000 | 1.4240 | 1.7091 | 2.7839 |
| 3 | 170 | 0 | 1 | 0.0000 | 4.5125 | 96.2176 | 0.2748 | 178225.0 | 1.0000 | 169.6950 | — | 1.0000 | 1.0426 | — | 0.8687 | 0.8832 | 1.0542 |
| 5 | 170 | 2 | 2 | 0.0118 | 6.1428 | 141.9412 | 0.6135 | 251174.5 | 0.9991 | 119.5185 | 0.0000 | 0.9991 | 1.1381 | 0.0000 | 1.2304 | 0.8285 | 1.4911 |
| 7 | 170 | 1 | 1 | 0.0059 | 9.1263 | 165.9059 | 0.6638 | 249663.0 | 0.9999 | 129.1510 | — | 0.9999 | 1.7158 | — | 4.5199 | 0.7856 | 2.8728 |
| 10 | 170 | 1 | 2 | 0.0059 | 8.1704 | 146.4294 | 0.6999 | 308642.0 | 0.9987 | 137.0113 | — | 0.9987 | 1.7436 | — | 1.3800 | 0.7533 | 2.0266 |
| 15 | 170 | 2 | 2 | 0.0118 | 8.3699 | 167.7176 | 0.7349 | 251174.5 | 0.9991 | 75.5070 | 0.0000 | 0.9991 | 0.9648 | 0.0000 | 1.3455 | 0.7391 | 1.8467 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.5002**

## mode = `agent`

| deletion_size | n_total | n_pass | n_admitted | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 17 | 2 | 1 | 0.1176 | 236.2859 | 288.0000 | 0.5225 | 1039827.3 | 1.0068 | 0.1020 | — | 1.0068 | 0.0048 | — | 1.9280 | 2.0568 | 2.9062 | 31.8462 |
| 3 | 9 | 0 | 0 | 0.0000 | 275.4833 | 0.0000 | 0.2198 | — | — | — | — | — | — | — | 0.8209 | 0.7461 | 0.9919 | 40.0000 |
| 5 | 9 | 2 | 0 | 0.2222 | 187.5122 | 128.4444 | 0.6317 | 1135488.5 | 0.9886 | 0.0930 | 0.0000 | 0.9886 | 0.0530 | 0.0000 | 0.9185 | 0.7992 | 1.2397 | 31.1250 |
| 7 | 9 | 3 | 0 | 0.3333 | 152.9778 | 287.2222 | 0.6811 | 864625.0 | 1.0000 | 0.0893 | 0.0000 | 1.0000 | 0.0338 | 0.0000 | 2.4324 | 0.8221 | 2.3281 | 27.2500 |
| 10 | 9 | 3 | 0 | 0.3333 | 144.7189 | 165.2222 | 0.6240 | 864042.7 | 0.9926 | 0.0907 | 0.0000 | 0.9926 | 0.0323 | 0.0000 | 0.9315 | 0.8927 | 2.5658 | 26.5000 |
| 15 | 9 | 3 | 0 | 0.3333 | 140.8844 | 146.3333 | 0.7094 | 864955.3 | 1.0041 | 0.0923 | 0.0000 | 1.0041 | 0.0354 | 0.0000 | 0.8362 | 0.8290 | 1.7283 | 26.5000 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.7585**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 170 | 17 | 0.1117 | -0.1461 | 31.8462 |
| 3 | 170 | 9 | 0.0000 | -0.0550 | 40.0000 |
| 5 | 170 | 9 | 0.2104 | 0.0182 | 31.1250 |
| 7 | 170 | 9 | 0.3274 | 0.0173 | 27.2500 |
| 10 | 170 | 9 | 0.3274 | -0.0759 | 26.5000 |
| 15 | 170 | 9 | 0.3215 | -0.0255 | 26.5000 |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | 0.4968 | -0.3595 |
| mean_n_assumptions_diff | — | -0.7857 |
| mean_normalized_edit_distance | 0.5794 | 0.4530 |
| mean_proof_chars_ratio | -0.3688 | 0.0786 |
| mean_proof_lines_ratio | -0.6109 | -0.7485 |
| mean_tactic_count_ratio | -0.1078 | -0.1294 |
| mean_vo_bytes_ratio | -0.1067 | 0.6564 |
| pass_rate | 0.7585 | 0.5002 |
