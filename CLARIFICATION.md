# AI Research Partner — 精简需求澄清

## 1. 核心目标

我希望有一个长期运行的 AI Research Partner。

我提供一个还很早期、甚至没有 formal definition 的 research idea / direction，系统和我一起持续把它推进成更清晰、更可验证的研究问题。

系统的目标不是单纯“帮我查资料”或“帮我写代码”，而是持续参与：

```text
idea
→ 理解问题
→ 调研相关工作
→ 提出 hypothesis
→ 验证 hypothesis
→ 分析结果
→ 修正理解
→ 产生新的 hypothesis
→ 继续探索
```

---

## 2. 支持非常早期的 research stage

系统不能假设一开始已经有：

- codebase；
- benchmark；
- 明确 metric；
- formalized problem；
- 清晰的实验设置。

一开始可能只有一句模糊想法。

系统需要能够从这种状态开始工作，并逐步帮助我：

- 搞清楚问题到底是什么；
- 找到已有相关工作；
- 判断哪些问题已经被研究过；
- 找到真正值得验证的 research question；
- 把模糊想法逐渐变成可验证 hypothesis。

---

## 3. Human 和 AI 都可以提出 hypothesis

Research 不应该只是 AI 执行我的 hypothesis。

我希望：

- 我可以随时提出新的 hypothesis、直觉或质疑；
- AI 也应该主动提出自己的 hypothesis；
- AI 可以提出和我不同甚至相反的解释；
- 双方提出的 hypothesis 都应该被后续研究和实验验证。

系统需要能够持续记住这些 hypothesis 以及它们的发展状态。

---

## 4. 系统应该以“验证 hypothesis”为核心

系统不能只是不断生成 idea。

对于重要 hypothesis，它应该进一步推动：

```text
hypothesis
→ 找相关 evidence
→ 找反例
→ 设计验证方式
→ 做实验 / 分析
→ 判断结果
→ 更新 hypothesis
```

尤其要重视能够区分不同解释的实验，而不是只追求 performance improvement。

---

## 5. Literature research 是持续过程

系统应该能够持续：

- 搜索论文；
- 阅读论文；
- 找和当前问题相关的 evidence；
- 找支持和反对当前 hypothesis 的工作；
- 发现新的 research gap；
- 根据新论文更新现有判断。

Literature research 不应该只发生在项目最开始，而应该贯穿整个研究过程。

---

## 6. 最终需要能够实际做实验

当问题逐渐清晰以后，系统应该能够从零开始：

- 建立实验代码；
- 修改代码；
- 运行实验；
- 调试；
- 读取结果；
- 分析结果；
- 根据结果决定下一步。

也就是说，即使最开始完全没有 repo，后面也能够自然进入实验阶段。

---

## 7. 希望复用 Claude Code 和 Codex

我目前已有：

- Claude Code Max 5x；
- GPT Plus / Codex。

我希望系统尽可能直接使用现有 Claude Code 和 Codex 的能力和订阅额度，而不是依赖大量额外的 API 调用。

Claude Code 和 Codex 应该能够被系统作为实际执行 research / coding / experiment 的 agent 使用。

---

## 8. 需要长期 research memory

系统需要长期记住整个研究过程，而不是依赖某一次 Claude Code 或 Codex session 的上下文。

至少应该记住：

- 当前 research direction；
- 做过哪些调查；
- 有哪些 hypothesis；
- 哪些 hypothesis 已经被支持、反驳或仍不确定；
- 做过哪些实验；
- 实验得到了什么结果；
- 哪些方向已经失败；
- 当前还有哪些关键 uncertainty；
- 为什么现在选择下一步研究。

换句话说，换掉一个 agent session 后，research 不能失忆。

---

## 9. Negative result 也要保留

系统不能只记住成功结果。

同样重要的包括：

- 被 falsify 的 hypothesis；
- 没有效果的实验；
- inconclusive result；
- 失败的研究方向；
- 已经尝试过但证明不值得继续的 idea。

这些结果应该影响后续 research，而不是被重复尝试。

---

## 10. 我需要持续参与和干预

系统不能完全脱离我自己运行。

我需要能够随时：

- 提出新的想法；
- 修改当前理解；
- 质疑 AI 的结论；
- 要求解释为什么做某件事；
- 改变优先级；
- 暂停某个方向；
- 要求进一步调查某个问题；
- 提出新的 hypothesis；
- 决定某个方向不再继续。

Human researcher 始终是 research loop 的一部分。

---

## 11. 需要一个前端作为长期交互入口

我希望系统有一个 Web frontend。

通过前端，我至少能够：

- 和 AI Research Partner 对话；
- 查看当前研究进展；
- 查看正在做什么；
- 查看已有 hypothesis；
- 查看实验状态和结果；
- 查看当前有哪些未解决的问题；
- 对当前任务进行干预；
- 提问“为什么现在要做这个”；
- 随时加入自己的判断。

这个前端不只是聊天窗口，而应该让我能够理解整个 research 当前处于什么状态。

---

## 12. 需要可观察性

我希望知道系统当前在做什么。

例如能够看到：

```text
正在读什么 paper
正在验证什么 hypothesis
正在跑什么实验
当前实验进行到哪里
AI 为什么选择这个任务
下一步准备做什么
```

不需要展示完整内部推理，但需要足够的信息让我能判断 research 是否跑偏。

---

## 13. 需要一定程度的 autonomy

我不希望每一步都要我手动批准。

系统应该可以自主：

- 搜资料；
- 阅读；
- 思考；
- 提 hypothesis；
- 做低成本验证；
- 分析已有结果；
- 继续推进明显合理的下一步。

但对于高成本、风险较大或明显改变研究方向的行为，我希望保留人为控制。

---

## 14. Research progress 的定义

我关心的不是：

```text
跑了多少实验
写了多少代码
读了多少论文
```

而是：

```text
我们是否对这个问题理解得更清楚了
是否排除了一些错误解释
是否发现了新的关键问题
是否找到了更值得研究的 hypothesis
是否逐渐形成了一个扎实的 research problem
```

---

## 15. 最终期望的使用体验

理想状态是：

我给系统一个很粗糙的 research idea。

之后我可以隔一段时间回来，看见：

```text
目前我们认为哪些事情比较可信
哪些 hypothesis 已经被反驳
哪些还没有结论
最近出现了哪些新 hypothesis
哪些论文改变了我们的理解
哪些实验已经完成
现在最大的 uncertainty 是什么
AI 认为下一步最值得研究什么
```

然后我继续加入自己的判断，research loop 再继续推进。

最终我希望得到的是：

> 一个能够长期和我共同探索 research direction、提出和验证 hypothesis、持续积累研究状态，并能逐渐从 early idea 走到实际实验的 AI Research Partner。
