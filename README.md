---
title: Lobels Stores AI Assistant
emoji: 🍪
colorFrom: yellow
colorTo: red
sdk: gradio
sdk_version: 5.49.1
python_version: "3.11"
app_file: app.py
pinned: false
---

# Lobels Biscuits — Stores AI Assistant

An AI assistant for Lobels Biscuits raw material stores data. Answers questions about:

1. Material consumption (daily and monthly)
2. Stock variances (losses and gains)
3. Reorder status
4. Category breakdowns and trends

The bot only answers from the loaded Lobels stores data — it never invents figures. All numbers come from SQL queries run in Python; GPT only phrases the answer.

## Tech stack

- Gradio (Hugging Face Spaces)
- Supabase Postgres
- OpenAI GPT-4o-mini

## Required secrets (in Space settings)

- SUPABASE_URL
- SUPABASE_SERVICE_KEY
- OPENAI_API_KEY

Powered by Netrisyl Insights
