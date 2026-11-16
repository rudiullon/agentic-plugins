# What a redeploy cannot undo

The moves in `principles.md` push toward speed: long leash, strip the
ceremony, product over prose, push to main. That is right for almost
everything, because almost everything a session gets wrong can be
fixed by shipping again. This file is about the remainder. It is short
on purpose.

The test for whether something belongs here is one question: **if
this is wrong in production, does the next deploy fix it?** If yes, it
is judgment, and the agent and the human can use judgment. If no, the
mistake stays behind in data, in a queue, or in what a client already
received, and it needs a line that is written down.

No model can infer these from the code. They are facts about how one
particular production system deploys and holds data. That is why they
are the part of an instruction file that does not go stale when the
model changes, and why the prune pass should leave them alone: a rule
guarding something irreversible keeps the old caution even when the
harness looks like it covers it.

## The lines

Each one is a default, with the premise it rests on. A project writes
its own version in its own terms, and drops a line whose premise does
not hold there.

1. **Schema changes expand first and contract later.** Drops and
   renames ship one release after the code stopped using the old
   shape.
   *Premise:* old and new code run side by side during a deploy.
   *Reopen when:* deploys stop the world, or there is no data yet.
2. **Responses to clients only add.** A new field ships with a
   default; nothing a client reads is removed or renamed in place.
   *Premise:* the client and the API do not go live at the same
   instant, or some clients cannot be updated at all (a mobile app
   already installed).
   *Reopen when:* one deploy ships both and nothing else consumes the
   API.
3. **Queued work outlives a deploy.** Keep the job name and accept
   the old payload for at least one release.
   *Premise:* jobs already in the queue were written by the old code.
   *Reopen when:* the project has no queue, or the queue is drained
   before every deploy.
4. **Data goes into production through a tool, not a runbook.** The
   tool has a dry run, an apply, and a verify, and was rehearsed
   against a sanitized copy.
   *Premise:* prose cannot be rehearsed or checked, and the copy is
   the only place a mistake is free.
   *Reopen when:* never for real user data; for a project with no
   users yet, the line has nothing to protect.
5. **Risky visible changes ship dark.** Behind a switch, turned on
   after someone has observed them working.
   *Premise:* turning a switch off is faster than a deploy and does
   not need the person who built it.
   *Reopen when:* a deploy is as fast and as safe as the switch.
6. **Production commands need a person.** An agent writes production
   data, promotes, or rolls back only when its person said so for
   that change.
   *Premise:* the consequences land on real users, and someone has to
   own them.
   *Reopen when:* this one is not a default. An instruction line is
   also not what enforces it: credentials the agent does not hold
   are.

## The same test, applied to tests

Move 9 says to verify against the running product and not to unit
test every micro decision, and it says this is not "no tests". The
question above draws the line between the two.

A test earns its place when it guards something a redeploy cannot
undo (security, privacy, sign-in, data integrity, the contract a
client depends on), or when it checks something a person trying the
product cannot see (a formula, a ranking, a counter, a limit shared
across processes). Everything else a person or an agent sees faster by
using the product, and a test for it is prose about the work in
another form.

Two habits keep the kept tests honest. Each one says, in a comment
above it, what it protects; and it has been seen failing for that
reason. A test that goes red and stays red is fixed or deleted the day
it is noticed, because a suite people have learned to ignore protects
nothing.

Cutting tests is a bet, not a fact. Whoever cuts says what went and
why in the commit, and watches whether a bug escapes that the deleted
test would have caught.

## How the audits use this

- **Setup audit, surface 4.** Does the project's instruction file say
  what a redeploy cannot undo here, in the project's own terms? A
  file with forty rules and none of these has its weight in the wrong
  place. A file that lists them for a project with no users yet is
  carrying lines with nothing to protect; say that too.
- **Session audit, moves 9, 10, and 12.** When the human stripped a
  gate or a test, was it one a redeploy could undo? Stripping ceremony
  around a copy change is move 10 done well. Stripping the rehearsal
  before a production data change is the opposite, and it scores as a
  finding whatever the outcome was.
- **Prune pass.** These lines are bucket 1 when their premise holds.
  The question to ask of each is the premise, not whether the model
  has improved.

## Where this came from

Written down during one project's platform reset, after a promotion
that carried many migrations at once and needed a long runbook to
land. One further project has adopted it since. Neither has measured
what the lines themselves prevented, so treat every line as a default
to test in your own project, not as a finding. If a line cost you more than it
protected, or a seventh earned its place, change this file.
