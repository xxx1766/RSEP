# 原生三节点补充验证（S10–S12 / 表S12）

33次正式运行、29,250个请求，全部保留。新实验为Tide自身在当前NJ三节点上的原生验证，未重建历史FaceNet/YOLOX图、异构硬件及KubeEdge/Ray扩缩容条件。历史图S2/S3/S8/S9保持原样。

`native_threefig_data.json`和`native_threefig_runs.csv`分别保留绘图采样与全部逐批次结果。三组60秒实验单列，不与30秒速率扫描合并。区间为三组结果的逐点t(2)95%区间，不裁掉越界端点。CPU计数为原生分配记录，含借用，不是利用率，也不表示模型内存已释放。

推荐直接运行上级目录的`slimmed_experiment_figures.ipynb`：16幅可编辑图、7个历史存档，数据内嵌，无须访问实验机器。S10–S12支持既有字体/线宽/画布控制。也可使用：

```sh
python plot_native_threefig.py --data native_threefig_data.json --out native-threefig-export
```

原始证据及独立核验位于项目`code/Tide/results-historical-threefig-20261019/`：原全通道审计`evidence/independent-final-33.json`保持false（首组长实验末端心跳覆盖527.91ms，阈值500ms）；`evidence/metric-qualification-33.json`单独核验请求/执行位置与100ms本地CPU快照。934个无效心跳样本、全部低goodput及基础设施中断记录保留，未插值心跳、重选结果或放宽原阈值。两种核验及数据哈希见`native_threefig_provenance.json`。
