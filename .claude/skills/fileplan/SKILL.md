---
name: fileplan
description: Run one declared fileplan transition end to end. Ask the tool for the contract, check the item against it, read what the contract names, then run the transition. Use when invoked as /fileplan, and whenever a composed session needs one transition run.
---

# /fileplan

One procedure for **any** transition the declaration holds — this repo's or a
consumer's. The tool holds the vocabulary. This skill only interprets the
vocabulary, and the sections the tool points at are the program
(`docs/method.md#the-interpreter`).

Invoked as `/fileplan <transition> <item>`. The transition and the item are
all you bring, and everything else is asked of the tool. **Nothing below
branches on which transition it is.** Reasoning from a transition's name
rather than from the tool's answer is the bug this procedure exists to
prevent.

## 1. Ask for the contract

```bash
uv run fileplan <transition> --help
```

What comes back is the options the run takes, and then the contract. The
contract says where the item goes, what the run `Requires` and `Refuses`, and
any keys it `Drops`. The contract also names the halves the run `Declares`
and a `Reading` block. A `Running it` block follows if the transition declares
a procedure (`docs/method.md#the-contract`).

**The contract is the example to follow.** The tool generates the contract
from the declaration you actually have. So the contract is right for a
transition nobody has ever seen before.

## 2. Ask whether this item passes

```bash
uv run fileplan <transition> <item> --check
```

Pass whatever options the contract said the run requires; a check is of a
**run**, so a required option is still required (`docs/method.md#the-check`).

* **rc 2** — report the refusal verbatim and **stop**. Nothing was written,
  and the message is the one a real run gives. Do not go on to read the
  sections or attempt the change: the point of asking first is not to spend
  the session on an item the transition was never going to take.
* **rc 0** — the sentence on stdout says what the run will do. Carry on.

The check is also the ownership guard: a claimed item held by another session
refuses here, before anything is read.

## 3. Read what the contract named

Every pointer in `Reading`, and the one in `Running it` if there is one. The
first says what the transition and the values it takes **mean**; the second
says what a session **does** around the run. If there is no `Running it`
block, there is nothing further to read — that silence is the declaration
saying so.

Read them before acting. They are the program.

## 4. Do what those sections say

Guided by the halves under `Declares`, never by the transition's name:

* **`claims`** — the run picks the item up for this session, and it is the run
  that takes it. Nothing takes a claim beforehand.
* **`mints`** — the run writes into the item's body in a strict form. Expect
  to mint first and fill the prose in afterwards.
* **`seeds`** — the item the run creates may be bodied from a declared
  template rather than from prose passed at the run; the option's own help
  says which templates the declaration holds.
* **`files`** — the run writes a **second item**, into the state the
  declaration names, carrying the two declared keys that say where it came
  from. It rides on `marks`, so the same run disposes of the bullet the new
  item was written from.
* **`marks`** — the run's subject is one **bullet** rather than the item, and
  what it writes onto that bullet is the declaration's own word. The offer for
  such a transition lists bullets, so the second handle it takes is a bullet's
  name.
* **`archives`** — the run files a record of the item. If the run also takes
  the file away, compose that record **in full before running**, because what
  it is written from goes with it.
* **`dissolves`** — the item's file is deleted by the run. Read whatever you
  still need out of it first; afterwards only git has it.
* **`absorbs`** — the edges pointing at this item are repointed at the
  survivor the run names, rather than cleared.

No half named is a half that does not happen. If the contract named none, the
run moves the item and writes nothing else.

## 5. Run it

The same command as step 2 with `--check` taken off, and the same options.

## 6. There is no step 6

The postconditions are the tool's. Every refusal a transition can raise is
computed **before** the run writes anything. A run that exited 0 has already
satisfied every refusal, so nothing is left to verify afterwards. What the run
did that you did not ask for, the run says on stderr. What the run wrote, the
run prints on stdout.

If the run refuses at rc 2 having passed the check, the tree changed under
you. Read the refusal and stop rather than retrying.
