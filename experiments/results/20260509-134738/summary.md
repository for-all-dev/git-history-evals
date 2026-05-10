# Experiment summary

Total runs: **368**

## mode = `baseline`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 51 | 0 | 0.0000 | 5.7222 | 165.7843 | 0.7041 | — | — | — | — | — | — | — | 2.5337 | 2.3460 | 4.6481 |
| 3 | 51 | 0 | 0.0000 | 5.7333 | 211.8627 | 0.3133 | — | — | — | — | — | — | — | 0.7351 | 0.8206 | 1.4630 |
| 5 | 51 | 0 | 0.0000 | 8.1955 | 312.8627 | 0.6148 | — | — | — | — | — | — | — | 0.8903 | 0.8035 | 1.8900 |
| 7 | 51 | 0 | 0.0000 | 15.4449 | 304.0392 | 0.6836 | — | — | — | — | — | — | — | 0.8402 | 0.7564 | 2.2429 |
| 10 | 51 | 0 | 0.0000 | 26.1765 | 304.0588 | 0.7728 | — | — | — | — | — | — | — | 0.7864 | 0.7342 | 2.2081 |
| 15 | 51 | 0 | 0.0000 | 9.7194 | 326.9216 | 0.8511 | — | — | — | — | — | — | — | 0.7682 | 0.7426 | 2.3170 |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **—**

## mode = `agent`

| deletion_size | n_total | n_pass | pass_rate | mean_inference_time_s | mean_output_tokens | mean_norm_edit_dist | mean_vo_bytes | mean_vo_ratio | mean_compile_s | mean_n_assumptions | vo_bytes_ratio | compile_time_ratio | n_assumptions_diff | proof_chars_ratio | proof_lines_ratio | tactic_count_ratio | mean_agent_n_turns |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| -1 | 17 | 0 | 0.0000 | 3.6571 | 0.0000 | 0.4911 | — | — | — | — | — | — | — | 1.4194 | 1.3892 | 2.1062 | — |
| 3 | 9 | 0 | 0.0000 | 3.4178 | 0.0000 | 0.2198 | — | — | — | — | — | — | — | 0.8218 | 0.7493 | 0.9919 | — |
| 5 | 9 | 0 | 0.0000 | 3.0133 | 0.0000 | 0.8396 | — | — | — | — | — | — | — | 0.5791 | 0.5183 | 0.6234 | — |
| 7 | 9 | 0 | 0.0000 | 2.5800 | 0.0000 | 0.8770 | — | — | — | — | — | — | — | 0.4805 | 0.4743 | 0.7510 | — |
| 10 | 9 | 0 | 0.0000 | 2.8089 | 0.0000 | 0.8818 | — | — | — | — | — | — | — | 0.4763 | 0.4765 | 0.7664 | — |
| 15 | 9 | 0 | 0.0000 | 2.6189 | 0.0000 | 0.8884 | — | — | — | — | — | — | — | 0.4730 | 0.4742 | 0.7797 | — |

Faithfulness correlation (Pearson r, deletion_size vs pass_rate): **—**

## baseline vs agent

| deletion_size | baseline_n | agent_n | Δpass_rate | Δnorm_edit_dist | Δmean_agent_n_turns |
|---|---|---|---|---|---|
| -1 | 51 | 17 | 0.0000 | -0.2130 | — |
| 3 | 51 | 9 | 0.0000 | -0.0935 | — |
| 5 | 51 | 9 | 0.0000 | 0.2248 | — |
| 7 | 51 | 9 | 0.0000 | 0.1934 | — |
| 10 | 51 | 9 | 0.0000 | 0.1090 | — |
| 15 | 51 | 9 | 0.0000 | 0.0373 | — |

## drift faithfulness (Pearson r vs deletion_size)

| metric | agent | baseline |
|---|---|---|
| mean_compile_time_ratio | — | — |
| mean_n_assumptions_diff | — | — |
| mean_normalized_edit_distance | 0.6857 | 0.5733 |
| mean_proof_chars_ratio | -0.8133 | -0.6688 |
| mean_proof_lines_ratio | -0.7782 | -0.6915 |
| mean_tactic_count_ratio | -0.6818 | -0.4583 |
| mean_vo_bytes_ratio | — | — |
| pass_rate | — | — |
