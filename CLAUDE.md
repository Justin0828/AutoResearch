# AutoResearch

一个长期运行的 AI Research Partner：从一句模糊的 research idea 出发，和人一起把它推进成可验证的研究问题，并最终能实际做实验。执行器是 Claude Code 自身（走 Max 5x 订阅，非 API）。

## 新 session 先读这三个文件

| 文件 | 是什么 |
|---|---|
| `CLARIFICATION.md` | 用户的原始需求澄清，15 条。所有设计的源头，不要改 |
| `DESIGN.md` | 模块级设计（v1.1+）。**唯一的设计真相来源**，改设计就改它 |
| `PROGRESS.md` | 当前进度、开放问题、下一步 |

`DESIGN.md` §0 是七条基础原则，决定架构形状；不理解它们就不要动代码。

## 不可违反的硬约束

这些是用户明确要求或经过讨论定下的，新 session 最容易在这里犯错：

1. **不污染宿主机**。不得 `sudo`、`apt`、全局 `pip install`、修改 shell 配置或 conda base。所有写入收敛在项目根与 autoresearch 根目录内，删掉即零残留。
2. **不得修改 `/etc/sing-box/config.json`**。它接管全机流量且与 Claude 的连接依赖它，配错会断网。改动必须写成 diff 交给用户 sudo 执行，并先准备回滚。
3. **环境隔离用 `uv` venv**，每个实验一个。不复用全局环境，不用 conda base。
4. **docker 不交给被编排的 agent**。用户在 docker 组，等价 root，会使所有目录限制失效。agent 只能提交 Dockerfile，由 orchestrator 审批后代为执行。
5. **调用 Claude Code 时不得使用 `--bare`**。它强制走 `ANTHROPIC_API_KEY`、不读 OAuth，会破坏"复用订阅额度"这个前提。
6. **设计变更必须同步回 `DESIGN.md`**，不要只存在于对话里。

## 环境事实（已探测，勿重复探测）

- **GPU**：2× RTX 5090 32GB（Blackwell / sm_120），driver 580.105，CUDA 13.0。**无 nvcc、无 torch**，环境干净。
- **工具**：`uv` ✓ `conda` ✓ `docker` ✓（panyz 在 docker 组）。48 核 / 251G 内存。
- **磁盘**：1.4T 可用，单盘。embodied 数据集是 TB 级，需预算管理。
- **网络**：sing-box 1.12 TUN 模式接管全部流量并限速，配置需 root。数据集下载按 UID 分流绕过（见 `DESIGN.md` M10）。
- **Agent CLI**：`claude` v2.1.280 已装；**`codex` 未安装**，双 executor 是后置可选项。
- **额度**：Max 5x，5 小时滚动窗口，到点强制结束 session。这是系统的基本节拍（`DESIGN.md` §0.5）。

## 首个研究方向

具身智能的上下层接口划分。上层（GPT6 Astra 类）提供视觉知识与任务拆解，下层是通用 action model。**不做上层**，聚焦 interface 给定前提下 action model 的**精细操作**能力。详见 `DESIGN.md` §2。

## 工作方式

- 用中文回复用户。
- 改设计先改 `DESIGN.md`，再动代码。
- 阶段推进后更新 `PROGRESS.md`。
- 决策要留下理由——这个项目本身就是关于"为什么"的可追溯性。
