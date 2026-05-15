# Experiment summary

Total runs: **903**

## mode = `baseline`

| deletion_size | n_total | n_pass | n_admitted | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 134 | 1 | 0 | 0.0075 | 13.7084 | 512.7985 | 0.6792 | 426533.0 | 0.9962 | 37.1390 | — | 0.9962 | 0.8686 | — | 1.4178 | 1.7162 | 2.6322 |
| 3 | 150 | 0 | 1 | 0.0000 | 3.9306 | 105.2933 | 0.2746 | 178225.0 | 1.0000 | 70.0610 | — | 1.0000 | 1.0771 | — | 0.8687 | 0.8827 | 1.0542 |
| 5 | 150 | 1 | 2 | 0.0067 | 5.3868 | 135.8733 | 0.6191 | 308642.0 | 0.9987 | 46.5863 | — | 0.9987 | 1.0143 | — | 1.5571 | 0.8134 | 1.6327 |
| 7 | 150 | 1 | 1 | 0.0067 | 8.3945 | 152.6467 | 0.6617 | 249663.0 | 0.9999 | 53.5330 | — | 0.9999 | 1.0737 | — | 2.4014 | 0.7838 | 2.2426 |
| 10 | 150 | 1 | 2 | 0.0067 | 8.0115 | 142.1933 | 0.6964 | 308642.0 | 0.9987 | 47.7827 | — | 0.9987 | 1.0594 | — | 0.8408 | 0.7635 | 1.7716 |
| 15 | 141 | 1 | 1 | 0.0071 | 9.7978 | 172.5177 | 0.7413 | 249663.0 | 0.9999 | 48.0590 | — | 0.9999 | 1.0305 | — | 1.2744 | 0.7622 | 2.2712 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.2623**

## mode = `agent`

| deletion_size | n_total | n_pass | n_admitted | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 11 | 1 | 0 | 0.0909 | 80.7036 | 19.3636 | 0.5761 | 319573.0 | 0.9951 | 0.0700 | — | 0.9951 | 0.0053 | — | 1.2137 | 1.0914 | 3.6215 | 2.0000 |
| 3 | 6 | 2 | 0 | 0.3333 | 92.8483 | 183.3333 | 0.2344 | 199284.0 | 0.9989 | 0.0610 | 0.0000 | 0.9989 | 0.0446 | 0.0000 | 0.8832 | 0.9389 | 1.0156 | 4.5000 |
| 5 | 6 | 1 | 0 | 0.1667 | 119.0883 | 60.1667 | 0.7142 | 319532.0 | 0.9950 | 0.0660 | — | 0.9950 | 0.0050 | — | 1.4181 | 1.1166 | 2.4151 | 4.0000 |
| 7 | 3 | 1 | 0 | 0.3333 | 93.8767 | 121.0000 | 0.5281 | 319532.0 | 0.9950 | 0.0690 | — | 0.9950 | 0.0052 | — | 0.9642 | 1.1569 | 2.9289 | 4.0000 |
| 10 | 1 | 1 | 0 | 1.0000 | 49.7900 | 367.0000 | 0.6000 | 319558.0 | 0.9950 | 0.0650 | — | 0.9950 | 0.0049 | — | 1.4352 | 2.0000 | 2.5000 | 4.0000 |
| 15 | 1 | 1 | 0 | 1.0000 | 45.0000 | 363.0000 | 0.6000 | 319536.0 | 0.9950 | 0.0690 | — | 0.9950 | 0.0052 | — | 1.2500 | 2.0000 | 2.5000 | 4.0000 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.8846**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 134 | 11 | 0.0834 | -0.1031 | 2.0000 |
| 3 | 150 | 6 | 0.3333 | -0.0402 | 4.5000 |
| 5 | 150 | 6 | 0.1600 | 0.0951 | 4.0000 |
| 7 | 150 | 3 | 0.3266 | -0.1336 | 4.0000 |
| 10 | 150 | 1 | 0.9933 | -0.0964 | 4.0000 |
| 15 | 141 | 1 | 0.9929 | -0.1413 | 4.0000 |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | -0.3102 | 0.5479 |
| mean_n_assumptions_diff | — | — |
| mean_normalized_edit_distance | 0.2759 | 0.4396 |
| mean_proof_chars_ratio | 0.2838 | -0.0648 |
| mean_proof_lines_ratio | 0.8491 | -0.7280 |
| mean_tactic_count_ratio | -0.1206 | 0.0475 |
| mean_vo_bytes_ratio | -0.3259 | 0.6164 |
| pass_rate | 0.8846 | 0.2623 |
