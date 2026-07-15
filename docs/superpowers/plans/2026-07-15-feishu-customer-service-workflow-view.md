# Feishu Customer Service Workflow View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current Feishu workflow with a disabled, review-ready workflow view that exposes RAG tutorials, SOP Skill inputs and slot collection, and a unified Final Reply path.

**Architecture:** Build one top-level customer-message flow with explicit preprocessing and intent routing. Route all customer-visible reply material through a central Final Reply node; expand SOP into Skill selection, slot collection, policy, TG/backend action, and result-summary stages.

**Tech Stack:** Feishu Base workflow builder, AI analysis/text nodes, Switch/If nodes, record actions, TG/backend action placeholders configured as real but disabled nodes.

## Global Constraints

- Preserve the existing “会话信息表” and “工单表”.
- Do not enable or run the workflow, send messages, or write test records.
- Keep the workflow master switch off throughout.
- Use readable business node names and labels.
- Produce a final full-canvas screenshot for the user.

---

### Task 1: Remove the rejected workflow

**Surface:** Existing Feishu workflow builder at the supplied Base URL.

- [ ] Open the current workflow menu and choose delete.
- [ ] Confirm deletion only for the workflow; do not delete either data table.
- [ ] Verify “会话信息表” and “工单表” remain visible.

### Task 2: Build preprocessing and first-level routing

**Produces:** A main chain with normalized data feeding a rewritten question and first-level intent.

- [ ] Add trigger “会话信息表新增记录且客户消息非空”.
- [ ] Add “消息标准化” node.
- [ ] Add independent “问题改写” node writing “改写问题”.
- [ ] Add “语言识别” node writing “语言类型”.
- [ ] Add `HUMAN_ACTIVE` guard that stops automated processing.
- [ ] Add intent routing with `RAG`, `SOP`, `情绪关怀`, `转人工`, and `无法识别`.

### Task 3: Build RAG and tutorial display

**Produces:** RAG path with a visible tutorial inventory and guarded answer material.

- [ ] Add RAG retrieval node reading “改写问题”.
- [ ] Put the four tutorial names in the node instruction: 账户与登录教程、身份认证教程、存款教程、提款教程.
- [ ] Add confidence decision and an RAG Reply Plan node.
- [ ] Route low-confidence or case-specific questions to clarification/SOP instead of inventing facts.

### Task 4: Build SOP Skill and slot collection detail

**Produces:** SOP dispatcher with three Skills and explicit parameters.

- [ ] Add SOP Skill selector for `deposit_missing`, `withdrawal_missing`, and `withdrawal_blocked_or_rollover`.
- [ ] Add shared “槽位提取与合并” node.
- [ ] Display deposit inputs: required `phone`, `receipt_screenshot`; optional `account_or_phone`, `customer_name`, `amount`, `payment_channel`.
- [ ] Display withdrawal inputs: required `phone`, `receipt_screenshot`; optional `account_or_phone`, `customer_name`, `amount`, `payment_channel`.
- [ ] Display blocked/rollover input: required `account_or_phone`.
- [ ] Add missing-slot decision and `WAITING_SUPPLEMENT` collection loop.
- [ ] Add completeness review and SOP Policy node.

### Task 5: Build TG/backend and unified Final Reply paths

**Produces:** Customer-facing text always passes through Final Reply.

- [ ] Add TG case summary/create action for deposit and withdrawal Skills.
- [ ] Add TG append-to-case action for supplements while waiting.
- [ ] Add backend query action for blocked/rollover.
- [ ] Add TG/backend result parsing and key-fact summary node.
- [ ] Add Reply Plan nodes for missing-data requests, waiting notices, supplements, RAG answers, emotion care, handoff, clarification, and results.
- [ ] Route every customer-visible Reply Plan to `Final Reply｜统一润色与安全出口`.
- [ ] Add command planning, record writeback, sending, and failure-to-human closure nodes after Final Reply.

### Task 6: Verify and capture the workflow image

**Produces:** Evidence that the workflow is complete and disabled.

- [ ] Inspect the canvas for visible RAG tutorials, SOP inputs/slots, TG result summary, and Final Reply convergence.
- [ ] Confirm no configured node displays “未完成设置”.
- [ ] Confirm the workflow switch remains visually off and no run log was created.
- [ ] Fit the complete workflow to the canvas.
- [ ] Capture and return a full-canvas screenshot to the user.
