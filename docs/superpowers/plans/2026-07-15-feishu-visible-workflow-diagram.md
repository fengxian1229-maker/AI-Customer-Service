# Feishu Visible Workflow Diagram Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a review-ready customer-service workflow image whose business names, Skill inputs, states, loops, and Final Reply convergence are visible directly on the diagram, then place it in the current Feishu Base without enabling the automation draft.

**Architecture:** Build one static labeled business-flow diagram from the approved workflow specification. Organize it into preprocessing, first-level routing, RAG, SOP, supporting routes, Final Reply, and downstream action zones; export a PNG and attach it to a dedicated record or image field in the supplied Feishu Base.

**Tech Stack:** HTML/SVG visualization, bundled render tooling, Feishu Base browser UI.

## Global Constraints

- Preserve “会话信息表”和“工单表”.
- Do not enable or run the automation workflow.
- Do not send messages or write test business records.
- Business labels and parameters must be readable without opening node details.
- Final Reply must visibly receive every customer-facing path.

---

### Task 1: Build the labeled workflow diagram

**Files:**
- Create: `/Users/andy/.codex/visualizations/2026/07/14/019f6055-3221-7341-816a-f48f4507ad0a/customer-service-workflow-map.html`

**Produces:** A static workflow view containing every required business node and edge.

- [ ] Add the preprocessing mainline: message input, normalization, question rewrite, language detection, and HUMAN_ACTIVE guard.
- [ ] Add first-level routes: RAG, SOP, emotion care, human handoff, and clarification.
- [ ] Show all four RAG tutorials directly on the diagram.
- [ ] Show all three SOP Skills and their required and optional parameters directly on the diagram.
- [ ] Add slot extraction, slot merge, missing-slot calculation, WAITING_SUPPLEMENT loop, SOP Policy, TG/backend actions, and result summaries.
- [ ] Connect every Reply Plan to `Final Reply｜统一润色与安全出口`.
- [ ] Add command planning, writeback, sending, retry, and failure-to-human closure.

### Task 2: Render and visually verify

**Files:**
- Create: `/Users/andy/.codex/visualizations/2026/07/14/019f6055-3221-7341-816a-f48f4507ad0a/customer-service-workflow-map.png`

**Produces:** A readable PNG suitable for Feishu.

- [ ] Render the HTML diagram to PNG.
- [ ] Inspect the PNG at original resolution.
- [ ] Verify node labels, parameters, arrows, state loops, and Final Reply convergence are not clipped or overlapped.
- [ ] Correct the source and rerender until the full diagram is readable.

### Task 3: Place the image in Feishu Base

**Surface:** The supplied Feishu Base.

**Produces:** A visible workflow-diagram record or view while preserving the disabled automation draft.

- [ ] Create a dedicated workflow-view table or record without modifying the two business tables.
- [ ] Add a title field and an attachment/image field.
- [ ] Upload the verified PNG and label it “AI客服完整业务流程图”.
- [ ] Confirm the image is visible from the Base and the automation still shows “保存并启用”.

### Task 4: Final verification

- [ ] Confirm the Base still shows “会话信息表”和“工单表”.
- [ ] Confirm the automation was not enabled or run.
- [ ] Confirm the diagram visibly includes RAG tutorials, SOP inputs, slot loop, TG/backend actions, Final Reply, and failure closure.
- [ ] Return the diagram to the user.
