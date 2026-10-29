# Tide

一种单体应用的分布式运行时，使应用资源空间像潮汐一样随着时间轮转实时动态自适应变化。

## Compile

```bash
go build -o bin/tide cmd/tide/main.go
```

## Usage

目前，启动Tide需要sudo权限，原因有二：
1. 目前实现采用的Linux消息队列作为Tide与应用的交互媒介，为突破Linux给消息队列默认设置的队列内消息数量、单个消息大小限制，在程序初始启动时，通过系统调用设置rlimit。
2. Tide需要创建与管理cgroup。

执行以下命令

```bash
sudo bin/tide --help
```

获得以下输出

```bash
Tide is a distributed computing framework

Usage:
tide [flags]
tide [command]

Available Commands:
help        Help about any command
serve       Start tide serve process
submit      submit job to tide system
```
其中 tide serve 为启动 Tide 系统的命令，tide submit 为提交任务至 Tide 系统的命令

### tide serve 命令

tide serve 命令的参数如下：

- addresses
    - 集群其他节点上Tide的访问地址（例，`198.51.100.14:10001`），多个地址以英文逗号分割
- cpu-num
    - Tide可使用的CPU核心数，超出总核心数时无效
- name
    - 节点名称，唯一标识设备，为空则会生成随机字符串
- outbound-ip
    - 对外暴露的ip, 为空会自动获取
- port
    - Tide ApiServer 监听的端口
- role
    - 设备在集群中的角色，有 things、edge、cloud 三类

- 运行时间（代码内部配置）：需要根据负载在特定资源限制下的运行时间，调整 `pkg/dev` 下的运行估计值，以达到更好的效果

### 例子

有三台设备，配置与角色如下表：

| Alias | IP          | Port  | Role   |
| --    | --          |  --   | --     |
| A     | 198.51.100.11 | 10001 | things |
| B     | 198.51.100.14 | 10001 | edge   |
| C     | 198.51.100.20  | 10001 | cloud  |

A设备启动
```bash
sudo bin/tide serve -name A -port 10001 -role things
```

B设备启动
```bash
sudo bin/tide serve -name B -port 10001 -role edge -addresses 198.51.100.11:10001
```

C设备启动
```bash
sudo bin/tide serve -name C -port 10001 -role cloud -addresses 198.51.100.11:10001,198.51.100.20:10001
```

在以上设备，依次执行后，即可完成三设备集群的搭建

### tide submit 命令

tide submit 支持向任意 things 节点提交任务，参数如下：

- server
    - 目标节点上Tide的访问地址（例 `198.51.100.14:10001`，如在本地提交即为 `127.0.0.1:10001`）
- image
    - 镜像名称
- command
    - 容器内任务启动命令，如 `"python3 /app/main.py"`（此处命令需要用双引号，防止启动命令中部分参数被 tide submit 解析）
- name
    - 应用名称，唯一标识应用，为空则会生成随机字符串
- volume
    - 挂载卷，格式为 `hostPath:containerPath`，如 `/xxx/zzz:/data`


### Cgroup 降级 (Ubuntu 22.04 LTS 为例)

> 参考：https://support.huaweicloud.com/intl/zh-cn/ucs_faq/ucs_faq_0032.html

Tide 目前代码中使用的 Cgroup 版本为 v1，而有些系统默认使用的是 v2，所以需要降级

```bash
sudo nano /etc/default/grub
```

- 找到`GRUB_CMDLINE_LINUX`

- 添加或修改`systemd.unified_cgroup_hierarchy`值，值为1，就是cgroup v2，值为0，则为cgroup v1

```bash
sudo grub-mkconfig -o /boot/grub/grub.cfg

reboot # 重启前确认其他用户工作不受影响
```