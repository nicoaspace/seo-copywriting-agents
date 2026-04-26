---
name: humanizer-english
version: 3.0.0
description: |
  Humanization layer (post-draft style filter) for English-language copy produced by the
  copywriting pipeline. Detects obvious AI-writing tells. Does NOT compete with the
  Copywriter Skill: if any rule here conflicts with SKILL.md, references/, the Brand DNA
  voice, an SEO requirement, a copywriting formula, a page-type structure, or the
  recognized exceptions, the Copywriter Skill always wins.
---

# Humanizer (EN) — Style review layer

## Priority and scope

**The Copywriter Skill always wins.** These rules are a **secondary layer** that only removes obvious AI-writing tells in aspects the copywriter skill does not regulate. When a conflict arises:

- SKILL.md, references/, Brand DNA voice, SEO requirements, copywriting formulas (AIDA, PAS, PASO, etc.), page-type structure, and the recognized exceptions → **prevail**.
- These rules → yield.

Apply these patterns at the sentence and paragraph level while writing, and as a final pass before delivering. The goal is for the text to read like a sector expert wrote it — not to dilute the brand voice or break the copywriting structure.

---

## Content patterns to avoid

### 1. Inflated significance, legacy, broader trends

Watch words: *stands as, serves as, is a testament/reminder, plays a vital/significant/crucial/pivotal/key role, underscores its importance, reflects broader, symbolizing its enduring/lasting, contributing to the, setting the stage for, marking a shift, key turning point, evolving landscape, focal point, indelible mark, deeply rooted.*

**Before:** The Statistical Institute was officially established in 1989, marking a pivotal moment in the evolution of regional statistics.
**After:** The Statistical Institute was established in 1989 to publish regional statistics independently.

### 2. Excessive emphasis on notability and media coverage

Watch words: *independent coverage, local/regional/national media outlets, written by a leading expert, active social media presence.*

**Before:** Her views have been cited in The New York Times, BBC, FT, and The Hindu. She maintains an active social media presence with 500,000 followers.
**After:** In a 2024 New York Times interview, she argued that AI regulation should focus on outcomes rather than methods.

### 3. Superficial -ing tail clauses

Watch words: *highlighting…, underscoring…, emphasizing…, ensuring…, reflecting…, symbolizing…, contributing to…, cultivating…, fostering…, encompassing…, showcasing…*

**Before:** The temple's color palette of blue, green, and gold resonates with the region's natural beauty, symbolizing local landscapes, reflecting the community's deep connection to the land.
**After:** The temple uses blue, green, and gold colors. The architect chose them in reference to local bluebonnets and the Gulf coast.

### 4. Vague attributions and weasel words

Watch words: *industry reports, observers have cited, experts argue, some critics argue, several sources/publications.*

**Before:** Experts believe the river plays a crucial role in the regional ecosystem.
**After:** The river supports several endemic fish species, according to a 2019 survey by the Chinese Academy of Sciences.

### 5. Formulaic "Challenges and Future Prospects" sections

Watch words: *Despite its… faces several challenges…, Despite these challenges, Challenges and Legacy, Future Outlook.*

Replace with concrete facts, dates, and named projects.

---

## Language and grammar patterns

### 6. Accumulated AI vocabulary

- **Overused connectors:** moreover, furthermore, additionally, however (overused), nonetheless, on the other hand, in fact, consequently, thus, hence, therefore (stacked).
- **Importance intros:** *it is important to note that, it should be mentioned that, it is worth noting that, it is essential to highlight that, it must be considered that.*
- **Stage-setting phrases:** *in today's fast-paced world, in the current landscape, in today's rapidly evolving environment, in this digital age.*
- **Fake depth:** *at its core, at its essence, fundamentally, ultimately, in essence.*
- **Formulaic closings:** *in conclusion, to summarize, to wrap up, all in all, as we have seen.*
- **Single-word AI tells:** *delve, leverage, garner, intricate, pivotal, tapestry, testament, underscore, vibrant, enduring, align with, foster, enhance, showcase, robust, seamless, holistic.*

**Before:** It is important to note that, in today's fast-paced world, digitization plays a pivotal role. Moreover, it is worth highlighting that this affects SMEs as well. In conclusion, organizations that fail to adapt will fall behind.
**After:** Digitization is no longer optional for SMEs. The ones still invoicing in spreadsheets aren't being cautious — they're piling up technical debt that costs twice as much to clear later.

### 7. Copula avoidance (avoiding is/are)

Watch phrases: *serves as, stands as, marks a, represents a, boasts, features, offers (when it just means "has").*

**Before:** Gallery 825 serves as LAAA's exhibition space. The gallery features four spaces and boasts over 3,000 square feet.
**After:** Gallery 825 is LAAA's exhibition space. It has four rooms totaling 3,000 square feet.

### 8. Negative parallelisms and trailing negations

Patterns: *Not only X but Y / It's not just about X, it's about Y.* Trailing-negation fragments like "no guessing" tacked onto a sentence.

**CTA exception:** "no contract / no commitment / no credit card / no setup fee / no hidden charges" are legitimate conversion hooks in pricing/landing/sales pages — do NOT flag them.

**Before:** It's not just about the beat under the vocals; it's part of the aggression. It's not merely a song, it's a statement.
**After:** The heavy beat reinforces the song's aggressive tone.

### 9. Elegant variation (synonym cycling)

Substituting 4 synonyms for the same concept in 4 consecutive sentences: *protagonist → main character → central figure → hero*.

**SEO exception:** moderate semantic variation (2-3 distinct forms across a long article) is allowed and supports ranking.

**Before:** The protagonist faces challenges. The main character must overcome obstacles. The central figure triumphs. The hero returns home.
**After:** The protagonist faces many challenges but eventually triumphs and returns home.

### 10. False ranges

Pattern: *from X to Y* where X and Y aren't on a meaningful scale.

**Before:** Our journey has taken us from the Big Bang to the cosmic web, from stellar birth to dark matter.
**After:** The book covers the Big Bang, star formation, and current theories about dark matter.

### 11. Passive voice and subjectless fragments

**Before:** No configuration file needed. Results are preserved automatically.
**After:** You don't need a configuration file. The system preserves results automatically.

### 12. Excessive hedging

Pattern: *could potentially possibly, might perhaps eventually, it may to some extent.*

**YMYL exception:** for legal, medical, financial, tax, or health content, hedging is **required** for compliance. Do NOT flag "may", "consult a professional", "subject to applicable law" in these contexts.

**Before:** It could potentially possibly be argued that the policy might have some effect on outcomes.
**After:** The policy may affect outcomes.

---

## Style patterns

### 13. Em dash overuse

More than 2 em dashes per 300 words usually signals AI. Replace with commas, periods, or parentheses.

**Before:** The term is promoted by institutions — not the people themselves — and persists — even in official documents.
**After:** The term is promoted by institutions, not the people themselves, and persists in official documents.

### 14. Mechanical bold emphasis on standalone phrases

Marking concepts in bold with no real hierarchical purpose. Limit bolding to genuinely structural use.

**Structural exception:** list items with bold headers (`- **Header:** text`) are allowed in listicles, FAQ pages, service pages, and comparison sections when the `references/` for the page_type prescribes them. Do NOT flag this pattern in those contexts.

### 15. Title Case in headings

Use sentence case for content headings.

**Before:** ## Strategic Negotiations And Global Partnerships
**After:** ## Strategic negotiations and global partnerships

### 16. Decorative emojis

Don't decorate headings or bullets with emojis unless the Brand DNA explicitly prescribes it.

### 17. Curly quotes

Use straight quotes (`"..."`) in HTML output rather than curly quotes (`"..."`).

### 18. Meta announcements

Watch phrases: *Let's dive in, Let's explore, Let's take a look at, Without further ado, Here's what you need to know, In what follows we will see.*

The AI announces what it's about to do instead of doing it. Start with the content directly.

### 19. Fragmented headings

An H2/H3 followed by a single-line paragraph that just rewords the heading. If the opening sentence adds nothing new, delete it.

### 20. Filler phrases

Quick swaps:
- "In order to achieve this goal" → "To achieve this"
- "Due to the fact that" → "Because"
- "At this point in time" → "Now"
- "In the event that" → "If"
- "The system has the ability to process" → "The system processes"
- "It is important to note that the data shows" → "The data shows"

### 21. Generic positive conclusions

Watch phrases: *The future looks bright, exciting times ahead, this represents a major step forward, opens up new possibilities.*

End with a specific fact, an actionable recommendation, or a real CTA — not a generic toast.

### 22. Chat artifacts

Watch phrases: *I hope this helps, Of course!, Certainly!, You're absolutely right!, Would you like…, let me know if you'd like me to expand…, Here is an overview of…*

These should never appear in published content.

### 23. Knowledge-cutoff disclaimers

Watch phrases: *as of my last training update, based on available information, while specific details are limited, I don't have access to real-time data.*

Out of the final text.

### 24. Sycophantic / servile tone

*Great question! You're absolutely right that this is a complex topic. That's an excellent point.*

Out.

---

## Recognized exceptions (do NOT flag as AI pattern)

Before flagging anything, verify it doesn't fall into one of these. If it does, it's not a violation:

1. **Persuasive vocabulary in sales/landing/pricing/product pages** — words like "innovative, leading, unique, exceptional, in the heart of" are acceptable when the Brand DNA supports them and the page has a commercial function. Only flag them if the Brand DNA explicitly lists them under "Avoided Terms" or if they fall under "Ethical Claims" (unverifiable superlatives, claims without proof).
2. **Structural bold-header lists** (`- **Concept:** explanation`) in listicles, FAQ pages, service pages, and comparison sections when the `references/` for the page_type prescribes them.
3. **Legally required hedging** in YMYL content (legal, medical, financial, tax, health).
4. **Triadic patterns inside copywriting formulas** (AIDA, PAS, three-part headlines, "faster, simpler, cheaper"). Rule of three is legitimate when it is structural to the formula.
5. **Conversion CTAs** with tag phrases like "no contract / no commitment / no credit card / no setup fee / no hidden charges".
6. **B2B / sector-specific terminology** prescribed by the Brand DNA: "implementation", "end-to-end management", "service delivery" when that's how the client and sector actually speak.
7. **Moderate semantic variation** (2-3 distinct forms for the same concept across a long article) that supports SEO.
8. **Controlled repetition of the primary keyword** in H1, first 100 words, and meta elements — that's an SEO requirement.
