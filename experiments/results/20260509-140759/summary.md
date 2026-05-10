# Experiment summary

Total runs: **350**

## mode = `baseline`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 48 | 1 | 0.0208 | 6.1179 | 167.0208 | 0.7041 | 69231.0 | 0.5559 | 89.2070 | 36.0000 | 0.5559 | 1.7260 | 36.0000 | 2.5337 | 2.3460 | 4.6481 |
| 3 | 51 | 0 | 0.0000 | 5.6986 | 209.4118 | 0.3133 | — | — | — | — | — | — | — | 0.7201 | 0.8050 | 1.4630 |
| 5 | 51 | 0 | 0.0000 | 9.8598 | 314.9608 | 0.6391 | — | — | — | — | — | — | — | 0.9302 | 0.8335 | 2.1013 |
| 7 | 48 | 0 | 0.0000 | 10.4900 | 305.5625 | 0.6868 | — | — | — | — | — | — | — | 0.8425 | 0.7652 | 2.2431 |
| 10 | 48 | 0 | 0.0000 | 16.4758 | 313.1250 | 0.7695 | — | — | — | — | — | — | — | 0.7959 | 0.7425 | 2.2118 |
| 15 | 48 | 0 | 0.0000 | 8.9917 | 338.5625 | 0.8452 | — | — | — | — | — | — | — | 0.7799 | 0.7486 | 2.3252 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **-0.6589**

## mode = `agent`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 16 | 1 | 0.0625 | 11.0850 | 13.3125 | 0.5457 | 319573.0 | 0.9951 | 0.0840 | — | 0.9951 | 0.0062 | — | 1.4660 | 1.3892 | 2.2291 | 2.0000 |
| 3 | 8 | 0 | 0.0000 | 2.5650 | 0.0000 | 0.2472 | — | — | — | — | — | — | — | 0.7928 | 0.7396 | 0.9908 | — |
| 5 | 8 | 0 | 0.0000 | 3.1738 | 0.0000 | 0.8465 | — | — | — | — | — | — | — | 0.5319 | 0.5242 | 0.9930 | — |
| 7 | 8 | 0 | 0.0000 | 3.0575 | 0.0000 | 0.8635 | — | — | — | — | — | — | — | 0.4500 | 0.4775 | 0.8657 | — |
| 10 | 8 | 0 | 0.0000 | 3.4112 | 0.0000 | 0.8651 | — | — | — | — | — | — | — | 0.4455 | 0.4733 | 0.8413 | — |
| 15 | 8 | 0 | 0.0000 | 8.6337 | 0.0000 | 0.8293 | — | — | — | — | — | — | — | 0.7413 | 0.4677 | 1.2105 | — |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **-0.6589**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 48 | 16 | 0.0417 | -0.1584 | 2.0000 |
| 3 | 51 | 8 | 0.0000 | -0.0661 | — |
| 5 | 51 | 8 | 0.0000 | 0.2074 | — |
| 7 | 48 | 8 | 0.0000 | 0.1767 | — |
| 10 | 48 | 8 | 0.0000 | 0.0956 | — |
| 15 | 48 | 8 | 0.0000 | -0.0159 | — |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | — | — |
| mean_n_assumptions_diff | — | — |
| mean_normalized_edit_distance | 0.6059 | 0.5618 |
| mean_proof_chars_ratio | -0.6027 | -0.6649 |
| mean_proof_lines_ratio | -0.7823 | -0.6895 |
| mean_tactic_count_ratio | -0.5457 | -0.4738 |
| mean_vo_bytes_ratio | — | — |
| pass_rate | -0.6589 | -0.6589 |
