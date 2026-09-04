# Combat Training Centre — badge requests

Discord-native intake and tracking for training badges, replacing the
Google Form → Sheet → Discord pipeline.

Each request becomes its own thread, created standalone so nothing lands in the
channel feed, with the ticket card as the first message inside it. The queue
channel becomes a list of open requests rather than a wall of embeds.

```
Member                          Instructor
------                          ----------
/badge request
  ↓ pick badges (up to 5)
  ↓ pick every level needed per badge
  ↓ optional notes
  └─→ one thread per badge ───→ [ Claim ]
                                  ↓
                               [ Badge Completed ] / [ All passed ]
                               [ Partial ] / [ Record result ]
                                  ↓
                               [ Open taw.net ]   ← the one manual step
                               [ Awarded ] ──────→ done, thread archived
```

## Setup

Add to `config.json` under `discord`:

```json
"combat_training_centre": {
  "queue_channel_id": 1111111111111111111,
  "instructor_role_id": 1111111111111111111,
  "config_role_id": 0,
  "assign_role_id": 0,
  "panel_role_id": 0,
  "taw_award_url": "https://www.taw.net/",
  "create_threads": true,
  "hide_thread_notices": true,
  "archive_on_award": true,
  "daily_bump": true,
  "lock_on_award": false,
  "nudge_unclaimed_hours": 48,
  "nudge_unawarded_hours": 72
}
```

Every key has a default, so a missing block will not raise — but
`queue_channel_id` must be set or requests have nowhere to go.

Any `*_role_id` accepts a single id or a list, so several roles can share an
ability:

```json
"panel_role_id": [1111111111111111111, 2222222222222222222]
```

| Key | Default | Notes |
| --- | --- | --- |
| `queue_channel_id` | — | Where request threads are created. Required. |
| `instructor_role_id` | `0` | Claim/complete/award, and the queue and stats commands. **`0` means anyone**, for local testing only. |
| `config_role_id` | `0` | Who may edit the catalogue. `0` falls back to the Manage Server permission. |
| `assign_role_id` | `0` | Who may set who is working on a request. `0` falls back to the instructor roles. |
| `member_role_id` | `0` | Who may raise a request with this unit. `0` means anyone. Staff can always request. |
| `panel_role_id` | `0` | Who may post the request panel. `0` falls back to the Manage Server permission. |
| `taw_award_url` | `""` | Deep link on completed tickets. Omitted if blank. |
| `create_threads` | `true` | `false` posts cards in the channel instead. |
| `private_threads` | `false` | `true` makes each request a private thread. See below. |
| `hide_thread_notices` | `true` | Deletes Discord's "started a thread" message. Needs Manage Messages. |
| `archive_on_award` | `true` | Archives the thread once awarded or cancelled. |
| `daily_bump` | `true` | Posts a silent daily line in open threads so they do not auto-archive. |
| `lock_on_award` | `false` | Also locks it, so only moderators can reopen. |

A Forum channel is detected automatically — each request becomes a forum post
and Discord posts no system message, so `hide_thread_notices` is unused there.

### Private threads

With `private_threads: true` a request is visible only to the member who raised
it, plus anyone holding **Manage Threads** on the channel. Grant that permission
to the instructor role and instructors see every request without the bot having
to add them to each one individually.

The bot needs **Create Private Threads** as well, and members are blocked from
inviting others (`invitable=False`). Discord posts no "started a thread" notice
for private threads, so `hide_thread_notices` is unused in this mode.

Public and private are fixed at creation and Discord cannot convert between
them, so switching the flag only affects new requests. Existing threads keep
whatever they were made as.

### Bot permissions on the queue channel

View Channel, Send Messages, Embed Links, Read Message History,
Create Public Threads, Create Private Threads, Send Messages in Threads,
Manage Messages (to delete thread notices), and Mention All Roles — the last
only if the instructor role is not set "Allow anyone to @mention this role",
otherwise pings render but never notify.

Permission integer `377957346304` covers all of those.

Members need **View Channel** on the parent channel; thread visibility is
inherited and cannot be granted separately. Deny them Send Messages and allow
Send Messages in Threads to keep the channel a clean list.

## Several battalions in one server

More than one unit can run its own training pipeline in the same Discord
server. Each gets its own badge catalogue, queue channel, instructors and
panel; nothing is shared unless you share it.

The guild cannot tell them apart on its own — both battalions have the same
`guild_id` — so every request, panel and interaction carries a **unit key**.

```json
"combat_training_centre": {
  "taw_award_url": "https://www.taw.net/",
  "private_threads": true,
  "primary_unit": "am2",
  "units": {
    "am2": {
      "name": "2nd Battalion",
      "catalogue": "catalogue.json",
      "queue_channel_id": 1111111111111111111,
      "instructor_role_id": [1111111111111111111, 2222222222222222222],
      "assign_role_id": 2222222222222222222,
      "config_role_id": 2222222222222222222,
      "member_role_id": 3333333333333333333
    },
    "am1": {
      "name": "1st Battalion",
      "catalogue": "am1.json",
      "queue_channel_id": 4444444444444444444,
      "instructor_role_id": [5555555555555555555],
      "member_role_id": 6666666666666666666
    }
  }
}
```

Settings resolve most-specific-first: **cog defaults → the shared block → the
unit's own block**. So `taw_award_url` and `private_threads` above apply to
both, while channels and roles stay per unit.

`catalogue` is a filename beside the code in `ctc/`. Each unit needs its own,
and `/badge config` only ever edits the one belonging to the unit the session
started in. `ctc/am1.json` ships as a copy of 2nd Battalion's, minus the
`extraTrainers` entries — those name specific people, and each battalion has
its own. The two files diverge from there.

### Which battalion is this?

Resolved in order, stopping at the first that answers:

1. the request thread the interaction is in — the row knows its own unit
2. the queue channel it was run in, or that channel's parent
3. the only configured unit, when there is just one
4. the member's own **staff** roles — instructor, config or assign — if they
   match exactly one unit

Somebody who staffs both battalions matches neither at step 4, so they are
asked to run the command in the right channel rather than being guessed at and
silently filed under the wrong one.

`member_role_id` is deliberately **not** used for routing. It says who may
request, and a division-wide "member" role would match a battalion it has
nothing to do with — silently filing the request under the wrong one. Failing
to route asks the member a question; misrouting is invisible.

Panels resolve without any of that: the button carries its unit in its
custom_id, so a panel can only ever open its own battalion's badges.

### Who may request

Set `member_role_id` (one id or a list) and only holders may raise a request
with that unit — plus its own staff, who can always request. Leave it unset and
requesting is open to everyone, which is how a single-battalion server has
always behaved.

The pinned panel enforces the same check, so it cannot be used to get around
it.

### Upgrading a single-battalion setup

Nothing to do. With no `units` block the whole config is read as one unit keyed
`default`, so an existing `config.json` keeps working untouched — which matters,
because that file lives on the host and is not deployed with the code.

On first start the `unit` column is added and every existing row is filed under
`primary_unit` (or the first unit listed). Panels pinned before the upgrade keep
working too: their old `ctc:panel:open` id resolves to the primary unit.

To split later, add the `units` block and set `primary_unit` to whichever key
represents the battalion that was already running — that is where the history
goes.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `/badge request` | Everyone | Open the picker |
| `/badge catalogue` | Everyone | Every badge, its levels and availability |
| `/badge queue [mine] [open]` | Instructors | Open request threads |
| `/badge stats` | Instructors | Status counts, load, turnaround |
| `/badge amend` | Instructors + extra trainers | Change what this thread's request needs |
| `/badge config` | Config role | Add, edit or retire badges |
| `/badge panel [catalogue]` | Manage Server | Post the pinnable request button |

`/badge queue` reads the **live threads** in the queue channel, not the
database — a thread that has been archived or deleted is finished, whatever the
database thinks.

## The three kinds of badge

| Kind | Config | Member picks | Instructor records |
| --- | --- | --- | --- |
| Tab | `levels: []` | Nothing | Run / not run |
| Graded | `levels: [...]` | Every level they need run | All passed, or which ones |
| Timed | `levels: [...]`, `timed: true` | Nothing | Exactly one level, by score |

Airborne, Radio and JTAC are Tabs. CQC and Gun Range are timed — the score sets
the level, so nothing is picked up front and exactly **one** level is awarded
per run. Everything else is graded, and a graded badge can clear several levels
in one session because that is several pieces of work rather than one score.

**Variants**: a badge can be run in several forms — Gun Range is Rifle, Pistol,
SMG, Shotgun or HMG. The member requests Gun Range; the instructor says which
was run when recording the result, and the database refuses a completion without
one.

## Hiding what is not ready

`"wip": true` keeps a badge in the catalogue but drops it out of the picker. It
is listed under "In development" instead.

`"wipLevels": ["A", "E"]` does the same for individual levels, so a badge can be
requestable at Basic while its higher levels are still being written:

```
Grenadier B · A, E in development
```

Those levels are not offered in the picker and a timed test cannot award them.
If every level lands in `wipLevels` the badge has nothing left to ask for, so it
drops out of the picker exactly as a fully `wip` badge would.

Existing tickets keep working either way; both flags only affect what can be
newly requested.

## One ticket per badge

Three badges in one submission creates **three** tickets sharing a `group_id`,
because each is claimed by a different instructor and finishes on a different
day. But the levels *within* one badge stay on a single ticket: Grenadier Basic
+ Advanced + Expert is one test session with one instructor.

## Closed threads and keeping open ones alive

A finished request's thread is renamed with a **`CLOSED: `** prefix, so the
channel can be read at a glance without opening anything. The rename happens
whether or not `archive_on_award` is on, and reopening a request takes the
prefix back off.

Names are capped at Discord's 100 characters, so a very long one loses its tail
to make room. Closing twice does not stack the prefix.

Open threads get a silent **"Bump to keep alive"** once a day at **00:00 UTC**.
Each day's bump replaces the previous one, so a thread carries exactly one
keep-alive line however long it stays open, and closing a request removes it.
Discord auto-archives an inactive thread, and a request waiting on someone's
availability can easily go quiet for longer than that; any message resets the
timer. The bump carries no mentions, so it keeps threads alive without
notifying anyone, and it skips threads that are already archived — posting into
one would silently unarchive it. Set `daily_bump` to `false` to turn it off.

The old bump is deleted only after the new one has posted, so the thread is
never briefly without a message holding the clock open. Posting and instantly
deleting would leave no trace at all, but whether Discord still counts a
deleted message as activity is undocumented, and a silent failure here means
threads quietly archive.

The message id is kept in `bump_message_id`. That column is added to existing
databases on startup — `CREATE TABLE IF NOT EXISTS` leaves a live table alone,
so the schema file only covers a fresh one.

## Setting who is working on a request

**Assign…** on an unclaimed ticket, **Reassign…** on a claimed one, opens a
picker and puts that person's name on it. Assigning unclaimed work claims it in
the same move, so there is no need to claim first and hand over second.

By default anyone who can claim can also assign. Set `assign_role_id` to narrow
it — to Training Specialists, say — and it then excludes everyone else,
instructors included.

The thread records who did it (`X assigned this to Y`, or `X reassigned this
from Y to Z`), so a reassignment is not mistaken for the trainer having picked
it up themselves. The person assigned is added to the thread, which matters on
a private thread they were never part of.

Awarded and cancelled requests cannot be assigned — there is nothing left to
work on. Reopen first.

## Editing the catalogue

`ctc/catalogue.json` is the source of truth — the picker, level dropdowns, queue
embeds and catalogue listing all read from it, so they cannot drift.

`/badge config` writes to it and hot-reloads. Edits are validated *before* they
reach disk, and the previous file is restored if a write somehow produced
something unloadable. Rejected: unknown level or category, duplicate key, a Tab
marked timed, a `wipLevels` entry the badge does not have, and more than 25
requestable badges (Discord's select menu limit).

Each badge's editor covers category, levels, levels held back, rename,
variants, extra trainers, timed, and availability.

Renaming keeps the old name in `formerNames` so historic rows still resolve.
Deleting warns how many requests reference the badge and offers "mark in
development" instead, which hides it from the picker without breaking history.

Editing the file by hand also works and is better for bulk changes; restart
afterwards.

### Extra trainers

Not every badge can be run by every instructor. `extraTrainers` names people or
roles pinged for one badge on top of the usual instructor roles:

```json
{ "key": "rotary", "extraTrainers": ["<@189362064995778560>"] }
```

Entries are literal Discord mentions — `<@id>` for a person, `<@&id>` for a
role — so one field covers both. Anything that is not a mention is rejected at
load, as is a list longer than 25.

Set it from **`/badge config` → pick a badge → Trainers**. The picker takes
people and roles in the same menu, and whoever is already set comes back
pre-selected. Picking replaces the whole list; choosing nothing, or the
**Clear** button, empties it. The badge editor shows the current list and the
count on the button, so it is visible without opening the screen.

Hand-editing `catalogue.json` works too, and is better for setting several
badges at once.

Currently set on Fixed Wing and Rotary.

**Extra trainers can work the tickets for their own badges** — claim, complete,
record a result, award, reopen, and `/badge amend` — exactly as an instructor
would. That is scoped to the badges that name them: being the Rotary specialist
grants nothing on a Medical ticket.

They are also added to the request thread when it is created, because on a
private thread permission to claim is no use without being able to see it. This
only works for named *people*: Discord cannot add a role to a thread, so a
role-based extra trainer still needs **Manage Threads** on the queue channel.

`/badge queue` and `/badge stats` remain instructor-only. They are guild-wide
views rather than per-badge, so an extra trainer works from the thread itself.

Panels posted by `/badge panel` are tracked in `ctc_panels`, and any that embed
the catalogue are re-rendered whenever `/badge config` changes it, so a pinned
panel cannot drift from the live list.

A panel posted before tracking existed is not in that table and will never
refresh. Adopt it with `/badge panel message_id:<id>`, run in its channel: the
message is rewritten in place and tracked from then on, so the pin survives.
Right-click the panel and Copy Message ID to get the value. A panel that has been deleted is dropped
from tracking the next time a refresh runs. Editing `catalogue.json` by hand and
restarting does not trigger a refresh; re-run `/badge panel` or make any change
through `/badge config`.

## Tests

```bash
python -m ctc.selftest
```

69 offline checks — catalogue validation, all three badge kinds, variants, the
full request lifecycle, amendments, and that every view fits Discord's component
limits. No Discord connection required.

## Known limits

- **taw.net is not integrated.** Awarding stays manual: the instructor gets a
  deep link and one button to confirm. Three manual steps become one.
- **No prerequisite or duplicate checking** — the bot cannot see what a member
  already holds on the website.
- **Badge history is not a record.** taw.net remains the source of truth; this
  tracks what needs doing, not what has been earned.
- **In-progress forms are in memory.** A half-finished picker is lost on
  restart; the member runs the command again. Submitted requests are unaffected.
