# Experiment summary

Total runs: **368**

## mode = `baseline`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 51 | 1 | 0.0196 | 6.0788 | 168.9020 | 0.7041 | 69231.0 | 0.5559 | 88.9730 | 36.0000 | 0.5559 | 1.6895 | 36.0000 | 2.5337 | 2.3460 | 4.6481 |
| 3 | 51 | 0 | 0.0000 | 4.5386 | 171.6471 | 0.3133 | — | — | — | — | — | — | — | 0.6992 | 0.7827 | 1.4630 |
| 5 | 51 | 0 | 0.0000 | 8.5037 | 314.5098 | 0.6147 | — | — | — | — | — | — | — | 0.9084 | 0.8237 | 1.9172 |
| 7 | 51 | 0 | 0.0000 | 13.3749 | 303.7451 | 0.6825 | — | — | — | — | — | — | — | 0.8367 | 0.7565 | 2.2298 |
| 10 | 51 | 0 | 0.0000 | 11.6412 | 306.6275 | 0.7707 | — | — | — | — | — | — | — | 0.7885 | 0.7382 | 2.2211 |
| 15 | 51 | 0 | 0.0000 | 21.0198 | 359.6863 | 0.8456 | — | — | — | — | — | — | — | 0.8032 | 0.7774 | 2.4760 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **-0.6589**

## mode = `agent`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 17 | 1 | 0.0588 | 30.8659 | 53.7059 | 0.5706 | 565018.0 | — | 0.1080 | — | — | — | — | 1.8791 | 1.8870 | 2.1229 | 4.0000 |
| 3 | 9 | 0 | 0.0000 | 41.2867 | 0.0000 | 0.2198 | — | — | — | — | — | — | — | 0.8132 | 0.7363 | 0.9919 | — |
| 5 | 9 | 1 | 0.1111 | 52.4656 | 31.8889 | 0.7581 | 319558.0 | 0.9950 | 0.0860 | — | 0.9950 | 0.0068 | — | 0.9761 | 0.7629 | 1.2345 | 3.0000 |
| 7 | 9 | 1 | 0.1111 | 41.9033 | 40.1111 | 0.8342 | 319532.0 | 0.9950 | 0.0950 | — | 0.9950 | 0.0075 | — | 0.6029 | 0.7156 | 1.0472 | 4.0000 |
| 10 | 9 | 1 | 0.1111 | 35.5167 | 40.3333 | 0.8030 | 319532.0 | 0.9950 | 0.0910 | — | 0.9950 | 0.0071 | — | 0.8674 | 0.7211 | 1.4145 | 4.0000 |
| 15 | 9 | 1 | 0.1111 | 41.1033 | 78.7778 | 0.7682 | 78979.0 | 1.0026 | 0.0820 | 0.0000 | 1.0026 | 0.1125 | 0.0000 | 0.5315 | 0.5587 | 0.9649 | 6.0000 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **0.6076**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 51 | 17 | 0.0392 | -0.1335 | 4.0000 |
| 3 | 51 | 9 | 0.0000 | -0.0935 | — |
| 5 | 51 | 9 | 0.1111 | 0.1434 | 3.0000 |
| 7 | 51 | 9 | 0.1111 | 0.1517 | 4.0000 |
| 10 | 51 | 9 | 0.1111 | 0.0323 | 4.0000 |
| 15 | 51 | 9 | 0.1111 | -0.0774 | 6.0000 |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | 0.8822 | — |
| mean_n_assumptions_diff | — | — |
| mean_normalized_edit_distance | 0.5472 | 0.5672 |
| mean_proof_chars_ratio | -0.7771 | -0.6486 |
| mean_proof_lines_ratio | -0.7492 | -0.6706 |
| mean_tactic_count_ratio | -0.6177 | -0.4168 |
| mean_vo_bytes_ratio | 0.8814 | — |
| pass_rate | 0.6076 | -0.6589 |
