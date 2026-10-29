# 审计证据

对应根目录 `论文主张与实现核查.md`，日期 2026-09-28。

- `source-sha256.json`：本轮核查时记录的 98 个正文/源码/脚本文件哈希，用于界定审计版本，不代表每个文件都经动态测试。
- `resource.go.txt`：未经修改的 `pkg/routine/resource.go` 副本。
- `resource_test.go.txt`：临时独立测试；确认 CPU-only 节点被现有 Greater(empty) 条件排除，以及二维资源的不可比较情况。
- `go.mod.txt`：临时 module，无第三方依赖。
- `resource-check.txt`：实际 `go test -v .` 输出。PASS 表示条件被验证，不表示缺陷已修复。

复查时把三个 `.txt` 文件复制到独立临时目录，分别命名为 `resource.go`、`resource_test.go`、`go.mod`，执行 `go test -v .`。不应将这些证据文件当作完整运行时或调度算法测试覆盖。
