# Setting the Bar

**A one-pager for the person who owns the decision — not the person running the tool.**

Someone on your team wants to run part of your work through a cheaper or faster
AI model, and keep the change only if quality holds up. They can measure the
quality. They cannot tell you how much is enough. **That number is yours**, and
this page is how to set it well. You do not need to understand the tool, the
models, or the math to do your part.

---

### Why it has to be you

The person running the test can tell you the new model is "94% accurate." Only
you know whether the 6% it gets wrong is a shrug or a serious problem — a
mislabeled ticket someone catches in a minute, or a wrong number on an invoice
that reaches a customer. The bar is a business judgment about the cost of being
wrong, and that cost is something only the person who owns the outcome can price.

### The one rule that matters: decide before you see the results

Set the bar **before** the test runs, in writing. This feels backwards, and it
is the entire point.

If you wait until the numbers are in, you will — without meaning to — pick a bar
that matches whatever you got. 91% accuracy feels fine if you were hoping for
90, and alarming if you were hoping for 99, and the number itself can't tell you
which you were hoping for. A bar set in advance is a standard. A bar set
afterward is a justification wearing the costume of one.

### How to find your number

Don't reach for a percentage that feels comfortable. Ask two plainer questions:

- **What does one wrong answer actually cost me, and how often can I afford it?**
  If a mistake costs ten minutes of someone's time, you can tolerate more of them
  than if a mistake costs a customer. Work from the consequence, not the comfort.
- **What happens today, without any of this?** You are not competing with
  perfection — you are competing with how things work now. If your own people
  disagree with each other 6% of the time on the same task, demanding 99% from a
  machine is asking it to be more consistent than the humans it replaces. Anchor
  to the real baseline.

### Two ways to get it wrong

| If your bar is… | What happens |
|---|---|
| **Too high** | You never approve anything, you keep overpaying, and the project dies of caution. |
| **Too low** | You ship errors that quietly cost more than you saved. |

The right bar lives between these, and finding it *is* the job. Setting it too
high is not the "safe" choice — it has a cost too, it's just a hidden one.

### The trap even careful leaders miss

Ask for the **worst case**, not just the average. A model that is "97% accurate
on average" can be getting one whole category of request wrong every single time
— and the average hides it completely. "How does it do on the hardest / most
important cases?" is a different question from "how does it do overall," and you
want both answered.

### What your sign-off actually buys you

When you set the bar in advance and put your name on it, the eventual decision
stops being a matter of opinion. Anyone — your team, your boss, an auditor — can
check the result against the standard you set and see plainly whether it was met.
That is what makes an AI-spend decision *defensible* instead of a judgment call
someone can second-guess later. You are not approving a model; you are
authoring the test it has to pass.

---

> **Your job, in one sentence.**
> Before the test runs, write down three numbers and the reason for each:
> **how good** it must be, **how bad the single worst case** may be, and **how
> much cheaper or faster** it must be to be worth switching — where every "why"
> is a sentence about what being wrong would cost you.

That's it. Hand those three numbers and their reasons to the person running the
test, and the tool does the rest.

---

*Want to see how these numbers become an automatic pass/fail check? That's
[`08-GUIDE-Creating-Rubrics.md`](08-GUIDE-Creating-Rubrics.md) — written for the person
who runs the test, using the bar you set.*
