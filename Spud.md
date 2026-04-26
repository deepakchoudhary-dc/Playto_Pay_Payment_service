1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it. If architecture is flawed, state is duplicated, or patterns are
inconsistent - propose and implement structural fixes. Ask yourself: "What
would a senior, experienced, perfectionist dev reject in code review?" Fix
all of it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

For tasks touching >5 independent files, you MUST launch parallel
sub-agents (5-8 files per agent). Each agent gets its own context window
(~167K tokens). This is not optional. One agent processing 20 files
sequentially guarantees context decay. Five agents = 835K tokens of working
memory.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


## 8. Self-Improvement

### Mistake Logging
After ANY correction from the user, log the pattern to a `gotchas.md`
file. Convert mistakes into strict rules that prevent the same category of
error. Review past lessons at session start before beginning new work.
Iterate until error rate drops to zero.

### Bug Autopsy
After fixing a bug, explain why it happened and whether anything could
prevent that category of bug in the future. Don't just fix and move on.

### Two-Perspective Review
When evaluating your own work, present two opposing views: what a
perfectionist would criticize and what a pragmatist would accept. Let the
user decide which tradeoff to take.

### Failure Recovery
If a fix doesn't work after two attempts, stop. Read the entire relevant
section top-down. Figure out where your mental model was wrong and say so.
If the user says "step back" or "we're going in circles," drop everything.
Rethink from scratch. Propose something fundamentally different.

### Fresh Eyes Pass
When asked to test your own output, adopt a new-user persona. Walk through
the feature as if you've never seen the project. Flag anything confusing,
friction-heavy, or unclear.