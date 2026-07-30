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
  "panel_role_id": 0,
  "taw_award_url": "https://www.taw.net/",
  "create_threads": true,
  "hide_thread_notices": true,
  "archive_on_award": true,
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
| `panel_role_id` | `0` | Who may post the request panel. `0` falls back to the Manage Server permission. |
| `taw_award_url` | `""` | Deep link on completed tickets. Omitted if blank. |
| `create_threads` | `true` | `false` posts cards in the channel instead. |
| `hide_thread_notices` | `true` | Deletes Discord's "started a thread" message. Needs Manage Messages. |
| `archive_on_award` | `true` | Archives the thread once awarded or cancelled. |
| `lock_on_award` | `false` | Also locks it, so only moderators can reopen. |

A Forum channel is detected automatically — each request becomes a forum post
and Discord posts no system message, so `hide_thread_notices` is unused there.

### Bot permissions on the queue channel

View Channel, Send Messages, Embed Links, Read Message History,
Create Public Threads, Send Messages in Threads, Manage Messages (to delete
thread notices), and Mention All Roles — the last only if the instructor role
is not set "Allow anyone to @mention this role", otherwise pings render but
never notify.

Members need **View Channel** on the parent channel; thread visibility is
inherited and cannot be granted separately. Deny them Send Messages and allow
Send Messages in Threads to keep the channel a clean list.

## Commands

| Command | Who | What |
| --- | --- | --- |
| `/badge request` | Everyone | Open the picker |
| `/badge catalogue` | Everyone | Every badge, its levels and availability |
| `/badge queue [mine] [open]` | Instructors | Open request threads |
| `/badge stats` | Instructors | Status counts, load, turnaround |
| `/badge amend` | Instructors | Change what this thread's request needs |
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

## One ticket per badge

Three badges in one submission creates **three** tickets sharing a `group_id`,
because each is claimed by a different instructor and finishes on a different
day. But the levels *within* one badge stay on a single ticket: Grenadier Basic
+ Advanced + Expert is one test session with one instructor.

## Editing the catalogue

`ctc/catalogue.json` is the source of truth — the picker, level dropdowns, queue
embeds and catalogue listing all read from it, so they cannot drift.

`/badge config` writes to it and hot-reloads. Edits are validated *before* they
reach disk, and the previous file is restored if a write somehow produced
something unloadable. Rejected: unknown level or category, duplicate key, a Tab
marked timed, more than 25 requestable badges (Discord's select menu limit).

Renaming keeps the old name in `formerNames` so historic rows still resolve.
Deleting warns how many requests reference the badge and offers "mark in
development" instead, which hides it from the picker without breaking history.

Editing the file by hand also works and is better for bulk changes; restart
afterwards.

## Tests

```bash
python -m ctc.selftest
```

32 offline checks — catalogue validation, all three badge kinds, variants, the
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
