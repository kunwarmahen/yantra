---
name: source-brief
description: Write up findings from several sources as a brief with
  citations and an explicit confidence note. Use when the answer runs
  longer than a couple of paragraphs, or when it draws on more than
  two or three sources.
allowed-tools: read_file, list_dir, glob, grep, web_fetch
---

# Writing a source brief

The shape below exists so a reader can check you. Every section is there
to answer a question they would otherwise have to ask.

## 1. The answer, first

Two or three sentences. What is true, as far as the sources say. Not a
summary of your process, not a list of what you looked at — the answer.
A reader who stops here should have what they came for.

## 2. What the sources say

One short block per source. Name it first (`notes/30-skills.md:14`,
`https://example.com/docs#caching`), then what it contributes. Quote
where the exact words matter; paraphrase where they do not. Do not merge
two sources into one claim — if they agree, say they agree; if they
disagree, that disagreement is the most interesting thing you found.

## 3. What is missing

The questions the sources do not settle, and for each one, what would
settle it: a file you could not read, a page that 404'd, a decision
recorded nowhere. This section is frequently the most valuable one, and
an empty one is a claim in itself — only write "nothing" when you mean
it.

## 4. Confidence

One line. High, medium or low, and *why* — how many independent sources,
how directly they address the question, how recent they are. "High:
three sources, all primary, all current" tells a reader something.
"High" alone does not.

## Habits that keep a brief honest

- **Never cite a file you did not read this session.** If you remember
  what it said, read it again; memory of a file is not a source.
- **Prefer primary sources.** The code over the note about the code, the
  note over your summary of it.
- **Quote the awkward part.** If a source nearly supports your answer but
  not quite, the near-miss belongs in the brief verbatim. That is exactly
  the sentence a reader needs to see.
