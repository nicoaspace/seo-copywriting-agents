# FAQ Pages

**Stage:** MOFU → BOFU
**Objective:** Answer the real questions your prospects and customers ask — directly, completely, and without filler. A great FAQ page reduces support load, builds trust, and captures high-intent long-tail search traffic.

---

## Guiding Principle

An FAQ page is not a dumping ground for questions nobody asked. It's a strategic asset that addresses real objections, reduces friction in the buying process, and captures search traffic from question-based queries. Every question should earn its place by answering something a real prospect has actually asked (via sales calls, support tickets, or search data).

**The test:** If a question on your FAQ page has never been asked by a real person, remove it. If the answer is vague or defensive, rewrite it.

---

## Recommended Structure

### 1. Page Headline
- Don't just use "Frequently Asked Questions" — add context.
- Connect it to the reader's goal: "Everything You Need to Know Before Getting Started" or "Common Questions About [Product/Service]."

❌ "FAQ"
✅ "Common Questions About Our Plans, Pricing, and Onboarding"

### 2. Category Organization
- Group questions by topic if you have more than 8-10 questions.
- Common categories:
  - **Getting Started** (onboarding, setup, first steps)
  - **Pricing & Billing** (plans, payment, refunds)
  - **Product / Service** (features, limitations, how it works)
  - **Support** (contact, response times, escalation)
  - **Security & Privacy** (data handling, compliance)
- Use H2 headings for each category, H3 for each question.

### 3. Question + Answer Format

**For each question:**
- **Question (H3):** Phrase it exactly as the user would ask it. Use natural language, not corporate jargon.
- **Answer:** Direct, complete, and honest. Lead with the answer, then add context.
  - First sentence = the direct answer
  - Following sentences = context, exceptions, or details
  - Final sentence = CTA or link if relevant

❌ Q: "What are your service capabilities?"
✅ Q: "Can I cancel my subscription anytime?"

❌ A: "Our platform leverages cutting-edge technology to provide a seamless experience."
✅ A: "Yes. You can cancel from your account settings at any time — no penalties, no phone calls. Your access continues until the end of your billing period."

### 4. CTA Integration
- Don't add a CTA to every answer — it feels salesy.
- Add contextual CTAs only where they're genuinely helpful:
  - Pricing questions → link to pricing page
  - Feature questions → link to product page or demo
  - Getting started → link to signup or onboarding guide
- End the full FAQ page with a catch-all CTA: "Still have questions? [Contact us / Book a call]"

---

## Writing Rules for FAQ Copy

1. **Answer the question in the first sentence.** Don't build up to it. The reader scrolled to this question specifically — respect their time.
2. **Use the user's language.** If they'd say "how much does it cost?", don't write "What is the pricing structure?"
3. **Be honest about limitations.** "We don't offer X yet, but here's what we do offer" builds more trust than dodging the question.
4. **Keep answers concise.** 2-5 sentences per answer is ideal. If an answer needs more, consider linking to a dedicated page.
5. **Don't repeat the question in the answer.** Jump straight to the response.
6. **Avoid legal-speak** unless it's genuinely required (privacy, compliance). Even then, translate it to plain language first.

---

## SEO for FAQ Pages

FAQ pages are powerful for long-tail keyword capture and featured snippets.

- **Target keywords:** Question-based queries: "how to [action]", "what is [term]", "does [product] [capability]", "can I [action] with [product]"
- **Title tag:** `[Product/Service] FAQ — Common Questions Answered | [Brand]`
- **H1:** Descriptive headline (not just "FAQ")
- **Meta description:** Mention the number of topics covered + key categories. 150-160 characters.
- **URL:** `/faq` or `/frequently-asked-questions`
- **Schema markup:** **FAQPage schema is mandatory.** This enables rich results in Google with expandable Q&A directly in search results.
- **Internal linking:** Link answers to relevant pages (pricing, product, blog, support docs).
- **Content length:** 1,000-2,000 words across all Q&As. Quality and coverage matter more than raw length.

### FAQPage Schema Example
```json
{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {
      "@type": "Question",
      "name": "Can I cancel my subscription anytime?",
      "acceptedAnswer": {
        "@type": "Answer",
        "text": "Yes. You can cancel from your account settings at any time..."
      }
    }
  ]
}
```

---

## Sourcing Questions

The best FAQ questions come from:
1. **Sales team:** What do prospects ask before buying?
2. **Support tickets:** What do customers ask most often?
3. **Search data:** What question-based keywords have volume? (Google Search Console, keyword tools)
4. **Competitor FAQs:** What do competitors answer that you don't?
5. **"People Also Ask" boxes:** Google's PAA for your target keywords.
6. **Onboarding feedback:** What confuses new users in the first 30 days?

---

## Adaptations by Business Type

| Business Type | FAQ Focus |
|---|---|
| SaaS / Subscription | Pricing, cancellation, data migration, integrations, security |
| Ecommerce | Shipping, returns, sizing, payment methods, order tracking |
| Service business | Process, timelines, deliverables, pricing models, guarantees |
| Healthcare | Insurance, eligibility, appointment process, privacy |
| Education / Courses | Curriculum, certification, time commitment, refund policy |

---

## Anti-patterns to Avoid

- ❌ Questions nobody has actually asked (invented to fill space)
- ❌ Answers that don't actually answer the question (deflection)
- ❌ Marketing copy disguised as FAQ answers
- ❌ No FAQ schema markup (missing free rich snippet opportunity)
- ❌ One giant unsorted list of 50+ questions with no categories
- ❌ Answers that say "Contact us for more information" without giving any actual information first
- ❌ Outdated answers that reference old pricing, features, or policies
- ❌ Duplicate content — answers that copy text verbatim from other pages
