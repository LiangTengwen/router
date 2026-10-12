# PD Program 调度收益验证：完整指标对比附录

由 `scripts/pd_program_validation/report_metrics.py` 根据已保存的原始数据生成。详见[通俗版详细报告](pd_program_scheduling_validation_detailed.md)。

每列为所列重复的单次运行指标中位数；计数不是重复总和。全部客户端分位数使用线性插值。N/A 表示未采集或不适用，不是 0。

早期无逐个 Program 样本，任务 p50/p95/p99 无法按统一算法补算；旧 lower p95 保留在 CSV/JSON 的 `legacy_task_p95_lower_seconds`。

`tasks_with_patch` 的具体口径、P cache 与 prompt hit 的分母、vLLM 两阶段统计相加的限制，见详细报告的指标说明。

[逐轮 CSV](pd_program_scheduling_validation_metrics.csv) · [逐轮 JSON 与来源哈希](pd_program_scheduling_validation_metrics.json)

## mock-initial / c4

| 对比项 | main（n=3） | candidate（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 0.553981 | 1.236749 |
| p50_task_duration_seconds | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A |
| run_wall_time_seconds | 2.223292 | 5.011818 |
| request_throughput_per_second | 57.572278 | 25.539632 |
| input_token_throughput_per_second | 14738.503226 | 6538.145840 |
| output_token_throughput_per_second | 230.289113 | 102.158529 |
| requests | 128 | 128 |
| successful_requests | 128 | 128 |
| failed_requests | 0 | 0 |
| input_tokens | 32768 | 32768 |
| output_tokens | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A |
| latency_mean_seconds | 0.055156 | 0.140746 |
| latency_p50_seconds | 0.055149 | 0.141335 |
| latency_p95_seconds | 0.062027 | 0.153245 |
| latency_p99_seconds | 0.063327 | 0.159448 |
| ttft_mean_seconds | 0.042549 | 0.128297 |
| ttft_p50_seconds | 0.042165 | 0.128633 |
| ttft_p95_seconds | 0.048737 | 0.140354 |
| ttft_p99_seconds | 0.049745 | 0.147090 |
| vllm_queue_time_mean_seconds | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 48 | 0 | 384 | 0 |

## mock-initial / c16

| 对比项 | main（n=3） | candidate（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 0.612078 | N/A |
| p50_task_duration_seconds | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A |
| run_wall_time_seconds | 0.648735 | 4.980102 |
| request_throughput_per_second | 197.307168 | 25.614815 |
| input_token_throughput_per_second | 50510.635036 | 6557.392632 |
| output_token_throughput_per_second | 789.228672 | 102.459260 |
| requests | 128 | 128 |
| successful_requests | 128 | 128 |
| failed_requests | 0 | 0 |
| input_tokens | 32768 | 32768 |
| output_tokens | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A |
| latency_mean_seconds | 0.063576 | 0.571884 |
| latency_p50_seconds | 0.061430 | 0.601759 |
| latency_p95_seconds | 0.087583 | 0.634426 |
| latency_p99_seconds | 0.093843 | 0.640826 |
| ttft_mean_seconds | 0.051659 | 0.559553 |
| ttft_p50_seconds | 0.049684 | 0.589047 |
| ttft_p95_seconds | 0.075179 | 0.623177 |
| ttft_p99_seconds | 0.082813 | 0.629012 |
| vllm_queue_time_mean_seconds | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 32 | 16 | 299 | 85 |
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## mock-dynamic-port / c4

| 对比项 | main（n=3） | candidate（n=3） | candidate-p4（n=3） |
|---|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.538257 | N/A | 0.554330 |
| p50_task_duration_seconds | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A |
| run_wall_time_seconds | 2.172341 | 5.074960 | 2.224867 |
| request_throughput_per_second | 58.922613 | 25.221875 | 57.531533 |
| input_token_throughput_per_second | 15084.188848 | 6456.799924 | 14728.072491 |
| output_token_throughput_per_second | 235.690451 | 100.887499 | 230.126133 |
| requests | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A |
| latency_mean_seconds | 0.053363 | 0.142558 | 0.055550 |
| latency_p50_seconds | 0.053196 | 0.141091 | 0.054022 |
| latency_p95_seconds | 0.060995 | 0.153563 | 0.064291 |
| latency_p99_seconds | 0.071195 | 0.163813 | 0.099935 |
| ttft_mean_seconds | 0.040964 | 0.130117 | 0.042920 |
| ttft_p50_seconds | 0.040840 | 0.129340 | 0.041270 |
| ttft_p95_seconds | 0.048140 | 0.141127 | 0.050343 |
| ttft_p99_seconds | 0.058030 | 0.151966 | 0.087038 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2；candidate-p4=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 39 | 9 | 337 | 47 |
| candidate-p4 | 48 | 0 | 384 | 0 |
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## mock-dynamic-port / c16

| 对比项 | main（n=3） | candidate（n=3） | candidate-p4（n=3） |
|---|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.608333 | N/A | 1.272014 |
| p50_task_duration_seconds | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A |
| run_wall_time_seconds | 0.641930 | 5.038734 | 1.351303 |
| request_throughput_per_second | 199.398650 | 25.048825 | 94.723421 |
| input_token_throughput_per_second | 51046.054273 | 6412.499260 | 24249.195855 |
| output_token_throughput_per_second | 797.594598 | 100.195301 | 378.893685 |
| requests | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A |
| latency_mean_seconds | 0.063097 | 0.578906 | 0.145526 |
| latency_p50_seconds | 0.061395 | 0.610020 | 0.146160 |
| latency_p95_seconds | 0.089281 | 0.636929 | 0.173055 |
| latency_p99_seconds | 0.099889 | 0.643003 | 0.210967 |
| ttft_mean_seconds | 0.051177 | 0.566521 | 0.132855 |
| ttft_p50_seconds | 0.049246 | 0.597372 | 0.133234 |
| ttft_p95_seconds | 0.073954 | 0.624707 | 0.159625 |
| ttft_p99_seconds | 0.087541 | 0.630117 | 0.197919 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2；candidate-p4=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 32 | 16 | 285 | 99 |
| candidate-p4 | 48 | 0 | 384 | 0 |
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## mock-20261010 / c4

| 对比项 | main（n=3） | candidate（n=3） | candidate-p32（n=3） | candidate-p4（n=3） |
|---|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.542646 | N/A | 0.552753 | 0.553732 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 2.178914 | 5.034424 | 2.216315 | 2.236763 |
| request_throughput_per_second | 58.744849 | 25.277335 | 57.753529 | 57.225555 |
| input_token_throughput_per_second | 15038.681248 | 6470.997874 | 14784.903353 | 14649.742163 |
| output_token_throughput_per_second | 234.979394 | 101.109342 | 231.014115 | 228.902221 |
| requests | 128 | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A | N/A |
| latency_mean_seconds | 0.053914 | 0.142676 | 0.055299 | 0.055716 |
| latency_p50_seconds | 0.053946 | 0.143518 | 0.054185 | 0.054488 |
| latency_p95_seconds | 0.060813 | 0.155851 | 0.061213 | 0.065661 |
| latency_p99_seconds | 0.069533 | 0.174069 | 0.087993 | 0.097536 |
| ttft_mean_seconds | 0.041330 | 0.130127 | 0.042528 | 0.043088 |
| ttft_p50_seconds | 0.041162 | 0.130825 | 0.041861 | 0.041707 |
| ttft_p95_seconds | 0.047561 | 0.143165 | 0.047824 | 0.049885 |
| ttft_p99_seconds | 0.056271 | 0.161865 | 0.074677 | 0.083907 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2；candidate-p32=0,1,2；candidate-p4=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 44 | 4 | 366 | 18 |
| candidate-p32 | 48 | 0 | 384 | 0 |
| candidate-p4 | 48 | 0 | 384 | 0 |
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## mock-20261010 / c16

| 对比项 | main（n=3） | candidate（n=3） | candidate-p32（n=3） | candidate-p4（n=3） |
|---|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.617866 | 4.817448 | 0.884677 | 1.240277 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 0.643658 | 5.110422 | 1.046884 | 1.321094 |
| request_throughput_per_second | 198.863513 | 25.046857 | 122.267635 | 96.889393 |
| input_token_throughput_per_second | 50909.059340 | 6411.995462 | 31300.514503 | 24803.684642 |
| output_token_throughput_per_second | 795.454052 | 100.187429 | 489.070539 | 387.557573 |
| requests | 128 | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A | N/A |
| latency_mean_seconds | 0.064041 | 0.588187 | 0.097289 | 0.141537 |
| latency_p50_seconds | 0.062298 | 0.612286 | 0.091123 | 0.139298 |
| latency_p95_seconds | 0.091121 | 0.638382 | 0.166459 | 0.184457 |
| latency_p99_seconds | 0.097735 | 0.647115 | 0.195023 | 0.224262 |
| ttft_mean_seconds | 0.051547 | 0.575714 | 0.084773 | 0.128997 |
| ttft_p50_seconds | 0.050227 | 0.599519 | 0.078941 | 0.126885 |
| ttft_p95_seconds | 0.079493 | 0.625590 | 0.153231 | 0.169962 |
| ttft_p99_seconds | 0.086342 | 0.634739 | 0.182708 | 0.211584 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；candidate=0,1,2；candidate-p32=0,1,2；candidate-p4=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| candidate | 48 | 0 | 384 | 0 |
| candidate-p32 | 48 | 0 | 384 | 0 |
| candidate-p4 | 48 | 0 | 384 | 0 |

## mock-20261011 / c4

| 对比项 | main（n=3） | balanced-p4（n=3） | balanced-p8（n=3） | original（n=3） | permit-fix（n=3） |
|---|---:|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.507425 | 0.522198 | 0.532696 | 1.233548 | 0.516658 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 2.050361 | 2.105227 | 2.155834 | 4.990545 | 2.096001 |
| request_throughput_per_second | 62.428040 | 60.801031 | 59.373771 | 25.648504 | 61.068667 |
| input_token_throughput_per_second | 15981.578196 | 15565.063944 | 15199.685476 | 6566.016941 | 15633.578776 |
| output_token_throughput_per_second | 249.712159 | 243.204124 | 237.495086 | 102.594015 | 244.274668 |
| requests | 128 | 128 | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A | N/A | N/A |
| latency_mean_seconds | 0.051370 | 0.053370 | 0.054568 | 0.142062 | 0.052626 |
| latency_p50_seconds | 0.051065 | 0.051500 | 0.052239 | 0.140495 | 0.050856 |
| latency_p95_seconds | 0.057422 | 0.059945 | 0.060629 | 0.153726 | 0.057550 |
| latency_p99_seconds | 0.062832 | 0.112748 | 0.092403 | 0.163984 | 0.104284 |
| ttft_mean_seconds | 0.040103 | 0.042280 | 0.043174 | 0.130535 | 0.041663 |
| ttft_p50_seconds | 0.039879 | 0.040305 | 0.040658 | 0.129082 | 0.040077 |
| ttft_p95_seconds | 0.045736 | 0.047760 | 0.049455 | 0.142147 | 0.046974 |
| ttft_p99_seconds | 0.048305 | 0.100583 | 0.078461 | 0.152204 | 0.093729 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；balanced-p4=0,1,2；balanced-p8=0,1,2；original=0,1,2；permit-fix=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| balanced-p4 | 48 | 0 | 384 | 0 |
| balanced-p8 | 48 | 0 | 384 | 0 |
| original | 48 | 0 | 384 | 0 |
| permit-fix | 48 | 0 | 384 | 0 |

## mock-20261011 / c16

| 对比项 | main（n=3） | balanced-p4（n=3） | balanced-p8（n=3） | original（n=3） | permit-fix（n=3） |
|---|---:|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 0.553156 | 0.805659 | 0.783108 | 4.674551 | 1.299427 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 0.578131 | 0.846640 | 0.836025 | 4.964902 | 1.385887 |
| request_throughput_per_second | 221.402980 | 151.185819 | 153.105553 | 25.780970 | 92.359628 |
| input_token_throughput_per_second | 56679.162899 | 38703.569787 | 39195.021622 | 6599.928250 | 23644.064749 |
| output_token_throughput_per_second | 885.611920 | 604.743278 | 612.422213 | 103.123879 | 369.438512 |
| requests | 128 | 128 | 128 | 128 | 128 |
| successful_requests | 128 | 128 | 128 | 128 | 128 |
| failed_requests | 0 | 0 | 0 | 0 | 0 |
| input_tokens | 32768 | 32768 | 32768 | 32768 | 32768 |
| output_tokens | 512 | 512 | 512 | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A | N/A | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A | N/A | N/A | N/A |
| latency_mean_seconds | 0.057258 | 0.088750 | 0.085939 | 0.572336 | 0.150544 |
| latency_p50_seconds | 0.055638 | 0.084474 | 0.083229 | 0.599672 | 0.151400 |
| latency_p95_seconds | 0.085772 | 0.131428 | 0.133083 | 0.618988 | 0.172695 |
| latency_p99_seconds | 0.091713 | 0.162963 | 0.138358 | 0.622054 | 0.227707 |
| ttft_mean_seconds | 0.047155 | 0.077876 | 0.074563 | 0.561001 | 0.139853 |
| ttft_p50_seconds | 0.045586 | 0.073522 | 0.072359 | 0.588432 | 0.140635 |
| ttft_p95_seconds | 0.076730 | 0.120695 | 0.119742 | 0.606476 | 0.161572 |
| ttft_p99_seconds | 0.080722 | 0.151892 | 0.125012 | 0.609572 | 0.215315 |
| vllm_queue_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A | N/A | N/A | N/A |

各列包含的 repeat：main=0,1,2；balanced-p4=0,1,2；balanced-p8=0,1,2；original=0,1,2；permit-fix=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| balanced-p4 | 48 | 0 | 384 | 0 |
| balanced-p8 | 48 | 0 | 384 | 0 |
| original | 48 | 0 | 384 | 0 |
| permit-fix | 48 | 0 | 384 | 0 |

## mock-tuned / c4

| 对比项 | main（n=3） | balanced-p8（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 0.503446 | 0.519422 |
| p50_task_duration_seconds | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A |
| run_wall_time_seconds | 2.046690 | 2.110716 |
| request_throughput_per_second | 62.540013 | 60.642919 |
| input_token_throughput_per_second | 16010.243202 | 15524.587311 |
| output_token_throughput_per_second | 250.160050 | 242.571677 |
| requests | 128 | 128 |
| successful_requests | 128 | 128 |
| failed_requests | 0 | 0 |
| input_tokens | 32768 | 32768 |
| output_tokens | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A |
| latency_mean_seconds | 0.050895 | 0.052947 |
| latency_p50_seconds | 0.050421 | 0.051526 |
| latency_p95_seconds | 0.057007 | 0.061022 |
| latency_p99_seconds | 0.074788 | 0.081235 |
| ttft_mean_seconds | 0.039785 | 0.042076 |
| ttft_p50_seconds | 0.039367 | 0.040344 |
| ttft_p95_seconds | 0.045429 | 0.049732 |
| ttft_p99_seconds | 0.060300 | 0.069603 |
| vllm_queue_time_mean_seconds | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A |

各列包含的 repeat：main=0,1,2；balanced-p8=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| balanced-p8 | 48 | 0 | 384 | 0 |

## mock-tuned / c16

| 对比项 | main（n=3） | balanced-p8（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 0.559078 | 0.588150 |
| p50_task_duration_seconds | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A |
| run_wall_time_seconds | 0.587156 | 0.621825 |
| request_throughput_per_second | 217.999801 | 205.845655 |
| input_token_throughput_per_second | 55807.948988 | 52696.487618 |
| output_token_throughput_per_second | 871.999203 | 823.382619 |
| requests | 128 | 128 |
| successful_requests | 128 | 128 |
| failed_requests | 0 | 0 |
| input_tokens | 32768 | 32768 |
| output_tokens | 512 | 512 |
| prefix_cache_hit_rate | N/A | N/A |
| prompt_token_hit_rate | N/A | N/A |
| latency_mean_seconds | 0.057986 | 0.061666 |
| latency_p50_seconds | 0.055802 | 0.056291 |
| latency_p95_seconds | 0.090598 | 0.112558 |
| latency_p99_seconds | 0.096585 | 0.130429 |
| ttft_mean_seconds | 0.047774 | 0.051342 |
| ttft_p50_seconds | 0.045870 | 0.045759 |
| ttft_p95_seconds | 0.079606 | 0.102792 |
| ttft_p99_seconds | 0.086780 | 0.119310 |
| vllm_queue_time_mean_seconds | N/A | N/A |
| vllm_prefill_time_mean_seconds | N/A | N/A |
| vllm_decode_time_mean_seconds | N/A | N/A |
| vllm_inference_time_mean_seconds | N/A | N/A |

各列包含的 repeat：main=0,1,2；balanced-p8=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 384 | 0 |
| balanced-p8 | 48 | 0 | 384 | 0 |

## 4921 / light

| 对比项 | main（n=3） | cohort（n=3） | optimized（n=3） | original（n=3） | token-aware（n=3） |
|---|---:|---:|---:|---:|---:|
| completed_tasks | 8 | 8 | 8 | 8 | 8 |
| failed_tasks | 0 | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 28.446166 | 28.449553 | 28.689672 | 27.186263 | 28.507210 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 58.088105 | 58.218286 | 58.456921 | 56.429409 | 58.219312 |
| request_throughput_per_second | 0.550887 | 0.549655 | 0.547412 | 0.567080 | 0.549646 |
| input_token_throughput_per_second | 1233.987580 | 1231.228284 | 1226.202120 | 1270.259623 | 1231.206571 |
| output_token_throughput_per_second | 70.513576 | 70.355902 | 70.068693 | 72.586264 | 70.354661 |
| requests | 32 | 32 | 32 | 32 | 32 |
| successful_requests | 32 | 32 | 32 | 32 | 32 |
| failed_requests | 0 | 0 | 0 | 0 | 0 |
| input_tokens | 71680 | 71680 | 71680 | 71680 | 71680 |
| output_tokens | 4096 | 4096 | 4096 | 4096 | 4096 |
| prefix_cache_hit_rate | 52.5234% | 52.5234% | 52.5234% | 52.5234% | 52.5234% |
| prompt_token_hit_rate | 52.5000% | 52.5000% | 52.5000% | 52.5000% | 52.5000% |
| latency_mean_seconds | 7.096479 | 7.097323 | 7.157353 | 6.781501 | 7.111739 |
| latency_p50_seconds | 6.722039 | 6.739141 | 6.834354 | 6.571031 | 6.682519 |
| latency_p95_seconds | 9.400540 | 9.360549 | 9.382682 | 8.346349 | 9.394458 |
| latency_p99_seconds | 9.420832 | 9.446089 | 9.463432 | 9.420174 | 9.486833 |
| ttft_mean_seconds | 1.153125 | 1.110931 | 1.163161 | 0.818951 | 1.135409 |
| ttft_p50_seconds | 0.788620 | 0.754627 | 0.872333 | 0.550969 | 0.749836 |
| ttft_p95_seconds | 3.435689 | 3.354011 | 3.349816 | 2.303921 | 3.345888 |
| ttft_p99_seconds | 3.476238 | 3.468906 | 3.468160 | 3.401171 | 3.463636 |
| vllm_queue_time_mean_seconds | 0.115014 | 0.112429 | 0.115092 | 0.070152 | 0.113396 |
| vllm_prefill_time_mean_seconds | 0.912901 | 0.878962 | 0.924321 | 0.541635 | 0.909684 |
| vllm_decode_time_mean_seconds | 5.953746 | 5.987845 | 5.976225 | 5.962097 | 5.974614 |
| vllm_inference_time_mean_seconds | 6.855722 | 6.864233 | 6.913951 | 6.503122 | 6.899945 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2；optimized=0,1,2；original=0,1,2；token-aware=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 24 | 0 | 96 | 0 |
| cohort | 24 | 0 | 96 | 0 |
| optimized | 24 | 0 | 96 | 0 |
| original | 24 | 0 | 96 | 0 |
| token-aware | 24 | 0 | 96 | 0 |

## 4921 / pressure

| 对比项 | main（n=3） | cohort（n=3） | optimized（n=3） | original（n=3） | token-aware（n=3） |
|---|---:|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 200.078248 | 78.616972 | 200.309142 | 200.229034 | 200.347151 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 223.762659 | 112.999304 | 223.913472 | 226.739884 | 223.907607 |
| request_throughput_per_second | 0.286017 | 0.566375 | 0.285825 | 0.282262 | 0.285832 |
| input_token_throughput_per_second | 2470.045728 | 4891.215975 | 2468.382077 | 2437.612606 | 2468.446730 |
| output_token_throughput_per_second | 36.610219 | 72.496022 | 36.585561 | 36.129506 | 36.586519 |
| requests | 64 | 64 | 64 | 64 | 64 |
| successful_requests | 64 | 64 | 64 | 64 | 64 |
| failed_requests | 0 | 0 | 0 | 0 | 0 |
| input_tokens | 552704 | 552704 | 552704 | 552704 | 552704 |
| output_tokens | 8192 | 8192 | 8192 | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 68.3787% | 0.0000% | 0.0000% | 0.0000% |
| prompt_token_hit_rate | 0.0000% | 68.3708% | 0.0000% | 0.0000% | 0.0000% |
| latency_mean_seconds | 50.004497 | 19.639137 | 50.062219 | 50.042194 | 50.071723 |
| latency_p50_seconds | 53.916023 | 9.185955 | 53.948984 | 54.691225 | 53.937694 |
| latency_p95_seconds | 55.948782 | 73.296720 | 56.008470 | 56.592110 | 56.000669 |
| latency_p99_seconds | 59.523122 | 86.873378 | 59.729608 | 57.814471 | 59.631412 |
| ttft_mean_seconds | 44.256225 | 13.606996 | 44.148785 | 44.140066 | 44.155394 |
| ttft_p50_seconds | 48.212707 | 3.123667 | 48.090093 | 48.733295 | 48.063901 |
| ttft_p95_seconds | 50.209427 | 67.354525 | 50.012490 | 50.644409 | 50.115985 |
| ttft_p99_seconds | 53.707465 | 80.825393 | 53.803242 | 52.009061 | 53.779760 |
| vllm_queue_time_mean_seconds | 36.017097 | 3.329938 | 17.043615 | 0.070757 | 17.054393 |
| vllm_prefill_time_mean_seconds | 7.658736 | 2.976560 | 7.661955 | 3.529391 | 7.661752 |
| vllm_decode_time_mean_seconds | 5.750693 | 6.007598 | 5.913451 | 5.901949 | 5.918696 |
| vllm_inference_time_mean_seconds | 13.410604 | 9.010703 | 13.575405 | 9.431340 | 13.580448 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2；optimized=0,1,2；original=0,1,2；token-aware=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| cohort | 48 | 0 | 192 | 0 |
| optimized | 48 | 0 | 192 | 0 |
| original | 48 | 0 | 192 | 0 |
| token-aware | 48 | 0 | 192 | 0 |

## 4921 / prefill-heavy

| 对比项 | main（n=1） | cohort（n=2） | optimized（n=1） | original（n=1） | token-aware（n=2） |
|---|---:|---:|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A | N/A | N/A |
| mean_task_duration_seconds | 195.766950 | 56.801203 | 195.759400 | 195.907477 | 195.753224 |
| p50_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p95_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| p99_task_duration_seconds | N/A | N/A | N/A | N/A | N/A |
| run_wall_time_seconds | 219.410794 | 76.706489 | 219.358037 | 222.537357 | 219.383050 |
| request_throughput_per_second | 0.291690 | 0.835632 | 0.291760 | 0.287592 | 0.291727 |
| input_token_throughput_per_second | 2519.037415 | 7216.520302 | 2519.643259 | 2483.645927 | 2519.356044 |
| output_token_throughput_per_second | 9.334090 | 26.740233 | 9.336334 | 9.202949 | 9.335270 |
| requests | 64 | 64 | 64 | 64 | 64 |
| successful_requests | 64 | 64 | 64 | 64 | 64 |
| failed_requests | 0 | 0 | 0 | 0 | 0 |
| input_tokens | 552704 | 552704 | 552704 | 552704 | 552704 |
| output_tokens | 2048 | 2048 | 2048 | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 68.5206% | 0.0000% | 0.0000% | 0.0000% |
| prompt_token_hit_rate | 0.0000% | 68.5126% | 0.0000% | 0.0000% | 0.0000% |
| latency_mean_seconds | 48.926673 | 14.185207 | 48.924786 | 48.961803 | 48.923241 |
| latency_p50_seconds | 53.829445 | 5.498828 | 53.832010 | 54.648273 | 53.885585 |
| latency_p95_seconds | 55.892496 | 55.622102 | 55.900570 | 56.496871 | 55.870914 |
| latency_p99_seconds | 56.439833 | 65.893980 | 56.470288 | 56.629999 | 56.442483 |
| ttft_mean_seconds | 47.549676 | 12.720986 | 47.530282 | 47.586932 | 47.525370 |
| ttft_p50_seconds | 52.459963 | 4.047566 | 52.456898 | 53.288443 | 52.487992 |
| ttft_p95_seconds | 54.511812 | 54.214714 | 54.505313 | 55.116343 | 54.487470 |
| ttft_p99_seconds | 55.065914 | 64.484179 | 55.065141 | 55.245737 | 55.039454 |
| vllm_queue_time_mean_seconds | 39.321238 | 4.102085 | 16.989194 | 0.005833 | 16.990805 |
| vllm_prefill_time_mean_seconds | 7.663527 | 3.159383 | 7.660616 | 3.531887 | 7.661759 |
| vllm_decode_time_mean_seconds | 1.379568 | 1.463299 | 1.390644 | 1.375941 | 1.390206 |
| vllm_inference_time_mean_seconds | 9.043094 | 4.622682 | 9.051260 | 4.907828 | 9.051966 |

各列包含的 repeat：main=0；cohort=0,1；optimized=0；original=0；token-aware=0,1。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 16 | 0 | 64 | 0 |
| cohort | 32 | 0 | 128 | 0 |
| optimized | 16 | 0 | 64 | 0 |
| original | 16 | 0 | 64 | 0 |
| token-aware | 32 | 0 | 128 | 0 |
**各列重复集合不同，不能直接用这些中位数计算配对收益。配对结论只使用相同 repeat 的 main。**
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## 4922 / prefill-heavy

| 对比项 | main（n=1） | cohort（n=2） | paced（n=2） |
|---|---:|---:|---:|
| completed_tasks | 16 | 16 | 16 |
| failed_tasks | 0 | 0 | 0 |
| tasks_with_patch | N/A | N/A | N/A |
| mean_task_duration_seconds | 195.625262 | 57.613820 | 52.940706 |
| p50_task_duration_seconds | 195.502978 | 67.535816 | 54.811450 |
| p95_task_duration_seconds | 218.723909 | 77.015546 | 70.485887 |
| p99_task_duration_seconds | 219.024408 | 78.262739 | 70.817492 |
| run_wall_time_seconds | 219.116337 | 78.583730 | 70.907277 |
| request_throughput_per_second | 0.292082 | 0.815219 | 0.902700 |
| input_token_throughput_per_second | 2522.422593 | 7040.229703 | 7795.715969 |
| output_token_throughput_per_second | 9.346633 | 26.087002 | 28.886395 |
| requests | 64 | 64 | 64 |
| successful_requests | 64 | 64 | 64 |
| failed_requests | 0 | 0 | 0 |
| input_tokens | 552704 | 552704 | 552704 |
| output_tokens | 2048 | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 68.0950% | 70.0101% |
| prompt_token_hit_rate | 0.0000% | 68.0871% | 70.0020% |
| latency_mean_seconds | 48.891249 | 14.388366 | 13.220103 |
| latency_p50_seconds | 53.789943 | 5.404240 | 3.210875 |
| latency_p95_seconds | 55.840394 | 57.581818 | 54.354381 |
| latency_p99_seconds | 56.413593 | 67.938587 | 62.910548 |
| ttft_mean_seconds | 47.467582 | 12.897442 | 11.725138 |
| ttft_p50_seconds | 52.357694 | 3.889793 | 1.674987 |
| ttft_p95_seconds | 54.438962 | 56.151568 | 52.927934 |
| ttft_p99_seconds | 55.001584 | 66.466104 | 61.425430 |
| vllm_queue_time_mean_seconds | 38.493703 | 4.007683 | 1.082458 |
| vllm_prefill_time_mean_seconds | 7.661110 | 3.195169 | 2.532679 |
| vllm_decode_time_mean_seconds | 1.426238 | 1.489737 | 1.494161 |
| vllm_inference_time_mean_seconds | 9.087348 | 4.684906 | 4.026841 |

各列包含的 repeat：main=0；cohort=0,1；paced=0,1。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 16 | 0 | 64 | 0 |
| cohort | 32 | 0 | 128 | 0 |
| paced | 32 | 0 | 128 | 0 |
**各列重复集合不同，不能直接用这些中位数计算配对收益。配对结论只使用相同 repeat 的 main。**
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## 4923 / pressure

| 对比项 | main（n=3） | bounded（n=2） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 199.647966 | 88.356375 |
| p50_task_duration_seconds | 199.584889 | 88.076578 |
| p95_task_duration_seconds | 222.753694 | 142.918698 |
| p99_task_duration_seconds | 223.048750 | 145.589130 |
| run_wall_time_seconds | 223.129523 | 146.265987 |
| request_throughput_per_second | 0.286829 | 0.437778 |
| input_token_throughput_per_second | 2477.054550 | 3780.649551 |
| output_token_throughput_per_second | 36.714102 | 56.035565 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 49.896928 | 22.074028 |
| latency_p50_seconds | 53.833884 | 7.027614 |
| latency_p95_seconds | 55.804642 | 106.075026 |
| latency_p99_seconds | 59.384794 | 122.405568 |
| ttft_mean_seconds | 44.135195 | 16.026232 |
| ttft_p50_seconds | 48.026273 | 0.844492 |
| ttft_p95_seconds | 50.028034 | 100.159607 |
| ttft_p99_seconds | 53.617914 | 116.269114 |
| vllm_queue_time_mean_seconds | 35.969535 | 0.454626 |
| vllm_prefill_time_mean_seconds | 7.641669 | 1.872136 |
| vllm_decode_time_mean_seconds | 5.764308 | 6.047946 |
| vllm_inference_time_mean_seconds | 13.405977 | 7.920083 |

各列包含的 repeat：main=0,1,2；bounded=0,1。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| bounded | 32 | 0 | 128 | 0 |
**各列重复集合不同，不能直接用这些中位数计算配对收益。配对结论只使用相同 repeat 的 main。**

## 4926 / pressure

| 对比项 | main（n=1） | serial（n=2） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 200.005160 | 136.362843 |
| p50_task_duration_seconds | 199.924412 | 136.720941 |
| p95_task_duration_seconds | 223.078556 | 240.922263 |
| p99_task_duration_seconds | 223.437247 | 241.579091 |
| run_wall_time_seconds | 223.541036 | 241.752242 |
| request_throughput_per_second | 0.286301 | 0.264736 |
| input_token_throughput_per_second | 2472.494583 | 2286.261709 |
| output_token_throughput_per_second | 36.646515 | 33.886232 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 49.986226 | 34.075647 |
| latency_p50_seconds | 53.851889 | 6.268874 |
| latency_p95_seconds | 55.871930 | 188.039849 |
| latency_p99_seconds | 59.564583 | 222.660580 |
| ttft_mean_seconds | 44.210675 | 28.275617 |
| ttft_p50_seconds | 48.090067 | 0.481635 |
| ttft_p95_seconds | 50.064205 | 182.290020 |
| ttft_p99_seconds | 53.744835 | 216.907115 |
| vllm_queue_time_mean_seconds | 36.085696 | 0.114447 |
| vllm_prefill_time_mean_seconds | 7.656235 | 1.512309 |
| vllm_decode_time_mean_seconds | 5.778107 | 5.800694 |
| vllm_inference_time_mean_seconds | 13.434343 | 7.313003 |

各列包含的 repeat：main=0；serial=0,1。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 16 | 0 | 64 | 0 |
| serial | 32 | 0 | 128 | 0 |
**各列重复集合不同，不能直接用这些中位数计算配对收益。配对结论只使用相同 repeat 的 main。**
**含失败、工作量不一致或缺少配对的诊断运行，不能据此认定收益。**

## 4927 / pressure

| 对比项 | main（n=3） | serial（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 199.750293 | 136.335160 |
| p50_task_duration_seconds | 199.707410 | 136.640220 |
| p95_task_duration_seconds | 222.876124 | 240.715949 |
| p99_task_duration_seconds | 223.133064 | 241.645061 |
| run_wall_time_seconds | 223.207386 | 241.885719 |
| request_throughput_per_second | 0.286729 | 0.264588 |
| input_token_throughput_per_second | 2476.190460 | 2284.979879 |
| output_token_throughput_per_second | 36.701294 | 33.867233 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 49.922509 | 34.068726 |
| latency_p50_seconds | 53.840119 | 6.282883 |
| latency_p95_seconds | 55.773593 | 187.647500 |
| latency_p99_seconds | 59.429849 | 222.274500 |
| ttft_mean_seconds | 44.127104 | 28.250519 |
| ttft_p50_seconds | 48.050852 | 0.481936 |
| ttft_p95_seconds | 50.027693 | 181.975730 |
| ttft_p99_seconds | 53.623111 | 216.460540 |
| vllm_queue_time_mean_seconds | 35.909495 | 0.046203 |
| vllm_prefill_time_mean_seconds | 7.645255 | 1.512988 |
| vllm_decode_time_mean_seconds | 5.828562 | 5.801336 |
| vllm_inference_time_mean_seconds | 13.487374 | 7.314324 |

各列包含的 repeat：main=0,1,2；serial=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| serial | 48 | 0 | 192 | 0 |

## 4927 / prefill-heavy

| 对比项 | main（n=3） | serial（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 195.494442 | 56.963172 |
| p50_task_duration_seconds | 195.387020 | 57.033409 |
| p95_task_duration_seconds | 218.585473 | 100.326657 |
| p99_task_duration_seconds | 218.881052 | 101.072612 |
| run_wall_time_seconds | 218.965573 | 101.268772 |
| request_throughput_per_second | 0.292283 | 0.631982 |
| input_token_throughput_per_second | 2524.159354 | 5457.793027 |
| output_token_throughput_per_second | 9.353068 | 20.223411 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 70.3648% |
| prompt_token_hit_rate | 0.0000% | 70.3566% |
| latency_mean_seconds | 48.858546 | 14.225732 |
| latency_p50_seconds | 53.786539 | 1.874582 |
| latency_p95_seconds | 55.825590 | 80.453306 |
| latency_p99_seconds | 56.354646 | 95.073921 |
| ttft_mean_seconds | 47.479001 | 12.831201 |
| ttft_p50_seconds | 52.395870 | 0.477791 |
| ttft_p95_seconds | 54.459691 | 79.096620 |
| ttft_p99_seconds | 54.988261 | 93.703320 |
| vllm_queue_time_mean_seconds | 39.058911 | 0.029705 |
| vllm_prefill_time_mean_seconds | 7.652828 | 1.511376 |
| vllm_decode_time_mean_seconds | 1.382180 | 1.390881 |
| vllm_inference_time_mean_seconds | 9.036292 | 2.902257 |

各列包含的 repeat：main=0,1,2；serial=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| serial | 48 | 0 | 192 | 0 |

## 4949 / pressure

| 对比项 | main（n=3） | cohort（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 200.190002 | 78.571640 |
| p50_task_duration_seconds | 200.029118 | 91.215757 |
| p95_task_duration_seconds | 223.286335 | 108.491810 |
| p99_task_duration_seconds | 223.745190 | 111.808498 |
| run_wall_time_seconds | 223.870568 | 112.647919 |
| request_throughput_per_second | 0.285879 | 0.568142 |
| input_token_throughput_per_second | 2468.855134 | 4906.473241 |
| output_token_throughput_per_second | 36.592573 | 72.722160 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 8192 | 8192 |
| prefix_cache_hit_rate | 0.0000% | 68.8043% |
| prompt_token_hit_rate | 0.0000% | 68.7963% |
| latency_mean_seconds | 50.032436 | 19.627817 |
| latency_p50_seconds | 53.932225 | 9.412302 |
| latency_p95_seconds | 55.924154 | 73.891279 |
| latency_p99_seconds | 59.507421 | 86.910587 |
| ttft_mean_seconds | 44.266127 | 13.563895 |
| ttft_p50_seconds | 48.157605 | 3.356218 |
| ttft_p95_seconds | 50.236973 | 67.735814 |
| ttft_p99_seconds | 53.786866 | 80.897783 |
| vllm_queue_time_mean_seconds | 36.059381 | 3.275965 |
| vllm_prefill_time_mean_seconds | 7.663528 | 2.895620 |
| vllm_decode_time_mean_seconds | 5.773017 | 6.059712 |
| vllm_inference_time_mean_seconds | 13.432744 | 8.960017 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| cohort | 48 | 0 | 192 | 0 |

## 4949 / prefill-heavy

| 对比项 | main（n=3） | cohort（n=3） |
|---|---:|---:|
| completed_tasks | 16 | 16 |
| failed_tasks | 0 | 0 |
| tasks_with_patch | N/A | N/A |
| mean_task_duration_seconds | 195.666392 | 58.669136 |
| p50_task_duration_seconds | 195.545787 | 70.633691 |
| p95_task_duration_seconds | 218.791427 | 78.894581 |
| p99_task_duration_seconds | 219.094535 | 80.688926 |
| run_wall_time_seconds | 219.180593 | 81.147541 |
| request_throughput_per_second | 0.291997 | 0.788687 |
| input_token_throughput_per_second | 2521.683114 | 6811.099787 |
| output_token_throughput_per_second | 9.343893 | 25.237980 |
| requests | 64 | 64 |
| successful_requests | 64 | 64 |
| failed_requests | 0 | 0 |
| input_tokens | 552704 | 552704 |
| output_tokens | 2048 | 2048 |
| prefix_cache_hit_rate | 0.0000% | 67.3856% |
| prompt_token_hit_rate | 0.0000% | 67.3778% |
| latency_mean_seconds | 48.901534 | 14.652198 |
| latency_p50_seconds | 53.826586 | 6.821700 |
| latency_p95_seconds | 55.877476 | 55.567898 |
| latency_p99_seconds | 56.415735 | 67.486925 |
| ttft_mean_seconds | 47.528183 | 13.198510 |
| ttft_p50_seconds | 52.455261 | 5.402332 |
| ttft_p95_seconds | 54.504718 | 54.198486 |
| ttft_p99_seconds | 55.040475 | 66.088475 |
| vllm_queue_time_mean_seconds | 39.259585 | 4.268589 |
| vllm_prefill_time_mean_seconds | 7.659645 | 3.332245 |
| vllm_decode_time_mean_seconds | 1.376922 | 1.452540 |
| vllm_inference_time_mean_seconds | 9.035859 | 4.781511 |

各列包含的 repeat：main=0,1,2；cohort=0,1,2。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 48 | 0 | 192 | 0 |
| cohort | 48 | 0 | 192 | 0 |

## 4917 / light

| 对比项 | main（n=1） |
|---|---:|
| completed_tasks | 8 |
| failed_tasks | 0 |
| tasks_with_patch | N/A |
| mean_task_duration_seconds | 29.510509 |
| p50_task_duration_seconds | N/A |
| p95_task_duration_seconds | N/A |
| p99_task_duration_seconds | N/A |
| run_wall_time_seconds | 60.108212 |
| request_throughput_per_second | 0.532373 |
| input_token_throughput_per_second | 1192.515927 |
| output_token_throughput_per_second | 68.143767 |
| requests | 32 |
| successful_requests | 32 |
| failed_requests | 0 |
| input_tokens | 71680 |
| output_tokens | 4096 |
| prefix_cache_hit_rate | N/A |
| prompt_token_hit_rate | N/A |
| latency_mean_seconds | 7.362557 |
| latency_p50_seconds | 6.907703 |
| latency_p95_seconds | 9.931796 |
| latency_p99_seconds | 9.959400 |
| ttft_mean_seconds | 1.210224 |
| ttft_p50_seconds | 0.801734 |
| ttft_p95_seconds | 3.827838 |
| ttft_p99_seconds | 3.838765 |
| vllm_queue_time_mean_seconds | N/A |
| vllm_prefill_time_mean_seconds | N/A |
| vllm_decode_time_mean_seconds | N/A |
| vllm_inference_time_mean_seconds | N/A |

各列包含的 repeat：main=0。

计数合计另外列出，避免中位数掩盖某一轮失败：

| 配置 | 完成任务合计 | 失败任务合计 | 成功请求合计 | 失败请求合计 |
|---|---:|---:|---:|---:|
| main | 8 | 0 | 32 | 0 |
