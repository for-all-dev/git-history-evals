# Experiment summary

Total runs: **1082**

## mode = `baseline`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 170 | 3 | 0.0176 | 15.1316 | 615.7941 | 0.6721 | 353594.0 | 0.7760 | 180.4363 | 36.0000 | 0.7760 | 2.1001 | 36.0000 | 1.4428 | 1.7421 | 3.0119 |
| 3 | 170 | 1 | 0.0059 | 4.4450 | 109.2941 | 0.2746 | 178225.0 | 1.0000 | 95.4050 | — | 1.0000 | 0.6011 | — | 0.8681 | 0.8821 | 1.0535 |
| 5 | 170 | 2 | 0.0118 | 6.9998 | 165.4412 | 0.6245 | 249663.0 | 0.9999 | 154.8510 | — | 0.9999 | 1.6206 | — | 5.4397 | 0.8212 | 2.8325 |
| 7 | 170 | 2 | 0.0118 | 7.5945 | 154.7176 | 0.6628 | 249663.0 | 0.9999 | 113.8680 | — | 0.9999 | 1.0606 | — | 2.4454 | 0.7902 | 2.3032 |
| 10 | 170 | 3 | 0.0176 | 8.4971 | 144.0471 | 0.6896 | 308642.0 | 0.9987 | 139.5753 | — | 0.9987 | 1.5474 | — | 0.8266 | 0.7544 | 1.7452 |
| 15 | 170 | 3 | 0.0176 | 8.2752 | 180.4941 | 0.7413 | 308642.0 | 0.9987 | 102.9603 | — | 0.9987 | 1.4014 | — | 1.9822 | 0.7624 | 2.5298 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.3516**

## mode = `agent`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 17 | 2 | 0.1176 | 47.5853 | 36.7059 | 0.5175 | 199276.0 | 0.9989 | 0.5305 | 0.0000 | 0.9989 | 0.0696 | 0.0000 | 2.0311 | 2.1349 | 3.9396 | 3.0000 |
| 3 | 9 | 0 | 0.0000 | 79.2267 | 0.0000 | 0.2198 | — | — | — | — | — | — | — | 0.8140 | 0.7363 | 0.9919 | — |
| 5 | 9 | 1 | 0.1111 | 70.6356 | 40.5556 | 0.7910 | 319558.0 | 0.9950 | 0.1030 | — | 0.9950 | 0.0025 | — | 6.1112 | 0.8883 | 3.1286 | 4.0000 |
| 7 | 9 | 1 | 0.1111 | 98.0556 | 40.5556 | 0.7893 | 319530.0 | 0.9950 | 0.0980 | — | 0.9950 | 0.0024 | — | 2.7250 | 0.8481 | 2.5905 | 4.0000 |
| 10 | 9 | 2 | 0.2222 | 73.4600 | 102.4444 | 0.7140 | 199255.5 | 0.9988 | 0.1000 | 0.0000 | 0.9988 | 0.0140 | 0.0000 | 0.7166 | 0.8790 | 1.6954 | 4.0000 |
| 15 | 9 | 1 | 0.1111 | 93.0956 | 40.3333 | 0.7963 | 319558.0 | 0.9950 | 0.0820 | — | 0.9950 | 0.0020 | — | 1.1077 | 0.8835 | 1.9855 | 4.0000 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.3718**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 170 | 17 | 0.1000 | -0.1546 | 3.0000 |
| 3 | 170 | 9 | -0.0059 | -0.0548 | — |
| 5 | 170 | 9 | 0.0993 | 0.1665 | 4.0000 |
| 7 | 170 | 9 | 0.0993 | 0.1265 | 4.0000 |
| 10 | 170 | 9 | 0.2046 | 0.0244 | 4.0000 |
| 15 | 170 | 9 | 0.0935 | 0.0550 | 4.0000 |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | -0.7523 | -0.1699 |
| mean_n_assumptions_diff | — | — |
| mean_normalized_edit_distance | 0.5942 | 0.4470 |
| mean_proof_chars_ratio | -0.2449 | -0.0220 |
| mean_proof_lines_ratio | -0.6043 | -0.7300 |
| mean_tactic_count_ratio | -0.4616 | -0.0862 |
| mean_vo_bytes_ratio | -0.4264 | 0.6544 |
| pass_rate | 0.3718 | 0.3516 |
