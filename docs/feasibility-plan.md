# Meeting Intelligence Feasibility Plan

## Goal

Demonstrate the feasibility of this flow:

~~~text
Sample transcript
-> AI-generated summary and structured task list
-> Power Automate
-> SharePoint/SP365-compatible task records
~~~

## Proofs

1. AI extracts explicit tasks without inventing assignees or deadlines.
2. Power Automate can consume the structured AI output.
3. Meeting and proposed-task records can be displayed in Microsoft Lists.

## Delivery Steps

1. Evaluate the AI Builder prompt with three anonymized transcripts.
2. Freeze the structured JSON output contract.
3. Build a manual Power Automate flow inside a solution.
4. Store meeting and task-proposal records in Microsoft Lists.
5. Run three end-to-end test cases and record the results.
6. Present the demo, limitations, and Option A/Option B recommendation.

## Decision Gate

Use AI Builder as Option A when prompt access, output quality, consistency, and
cost are acceptable. Use the external Python API as Option B only when stronger
validation, model control, transcript processing, or deployment flexibility is
required.

## Non-Goals

- Live audio capture or speech-to-text
- A meeting bot
- Production deployment
- Automatic approval of AI-proposed tasks
- Final SP365 integration without an approved API and field contract
