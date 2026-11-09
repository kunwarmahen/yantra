"""A phone skill run on the other kind of phone.

The bias these tests encode: A SKILL KEEPS EACH PHONE'S WAY. A skill
taught on an iPhone and first run on an Android phone is written up once
more, as that phone's own section, with the iPhone's steps kept word for
word -- never repaired over them, which would make the two phones undo
each other's fixes run after run. Once a phone has its section, or the
run is on the phone it was taught on, nothing more is written. The
phones a skill covers are read from its own text, so the person can see
them, and the model is told to follow a phone's section on that phone.
"""

from __future__ import annotations

from conftest import assistant_text, assistant_tool_call
from test_learned_skills import learner_after, make_agent
from yantra.skills.learn import phone_kinds, taught_kind
from yantra.skills.loader import SKILL_FILE
from yantra.skills.registry import YOURS_FOOTER

TAUGHT = ("# Check my battery\n\nTaught on an iPhone; on an Android phone the screens "
          "may differ.\n\n1. Open the **Settings** app (open_app \"Settings\").\n"
          "2. Tap **Battery**.\n3. Read the percentage.\n")


def phone_skill(tmp_path, body=TAUGHT):
    folder = tmp_path / "home/.yantra/skills/learned/check-battery"
    folder.mkdir(parents=True)
    (folder / SKILL_FILE).write_text(
        "---\nname: check-battery\ndescription: Read the phone's battery level in "
        "its Settings app. Use when asked how charged the phone is.\norigin: learned\n"
        "learned: 2026-11-01 · worked 3 · failed 0\n---\n\n" + body)
    return folder


def on_phone(agent, kind):
    agent.prompt.set("phone", f"You can work the person's {kind} (Nexus 6P, in use).")


def followed(worked=True):
    """Load the skill, two phone steps (stand-ins), maybe a third way, and an answer."""
    steps = [assistant_tool_call("l", "load_skill", {"name": "check-battery"}),
             assistant_tool_call("a", "bash", {"command": "echo opened Settings"})]
    if worked:
        steps.append(assistant_tool_call("b", "bash", {"command": "echo Battery 81%"}))
    else:
        steps += [assistant_tool_call("b", "bash", {"command": "echo no Battery row && exit 3"}),
                  assistant_tool_call("c", "bash", {"command": "echo Device care, Battery 81%"})]
    return [*steps, assistant_text("81%.")]


def section(kind="Android phone"):
    return f"""VERDICT: update
NAME: anything
DESCRIPTION: Read the phone's battery level in Settings. Use when asked how charged it is.
SCOPE: user
INPUTS: none
NEEDS: none
=== INSTRUCTIONS ===
{TAUGHT}
## On an {kind}

2. In place of step 2: tap **Battery and device care**, then **Battery**.
=== END ===
"""


class TestWhichPhones:
    def test_the_phones_a_skill_covers_are_read_from_its_text(self):
        assert taught_kind(TAUGHT) == "iPhone"
        assert phone_kinds(TAUGHT) == {"iPhone"}
        assert phone_kinds(TAUGHT + "\n## On an Android phone\nThe same steps.") == \
            {"iPhone", "Android phone"}
        assert phone_kinds("1. Run the script.") == set()

    def test_the_model_is_told_to_follow_a_phones_own_section(self):
        assert "## On an Android phone" in YOURS_FOOTER


class TestTheOtherPhone:
    def test_a_first_run_that_worked_writes_that_phones_section(self, tmp_path):
        phone_skill(tmp_path)
        agent = make_agent(tmp_path, [*followed(), assistant_text(section())])
        on_phone(agent, "Android phone")
        learner = learner_after(agent, "how charged is my phone")
        assert learner.last_counted == [("check-battery", True)]
        assert learner.other_phone == ("check-battery", "Android phone", "iPhone", True)
        offer = learner.consider()
        assert offer is not None, learner.last_skip
        sent = agent.provider.last_request()["messages"][0].text()
        assert "on a phone it had no steps for yet, and it worked" in sent
        assert "THIS RUN WAS ON Android phone" in sent
        assert "Do not change the steps for iPhone: copy them word for word" in sent
        assert offer.repairs.name == "check-battery" and offer.draft.name == "check-battery"
        assert "+## On an Android phone" in offer.diff()
        assert "-2. Tap **Battery**." not in offer.diff()       # the iPhone's kept

    def test_once_it_has_the_section_nothing_more_is_written(self, tmp_path):
        phone_skill(tmp_path, TAUGHT + "\n## On an Android phone\n\nThe same steps work.\n")
        agent = make_agent(tmp_path, followed())
        on_phone(agent, "Android phone")
        learner = learner_after(agent, "how charged is my phone")
        assert learner.other_phone is None
        assert learner.consider() is None
        assert "already followed a skill" in learner.last_skip

    def test_on_the_phone_it_was_taught_on_nothing_is_written(self, tmp_path):
        phone_skill(tmp_path)
        agent = make_agent(tmp_path, followed())
        on_phone(agent, "iPhone")
        learner = learner_after(agent, "how charged is my phone")
        assert learner.other_phone is None and learner.consider() is None

    def test_a_failure_on_the_other_phone_is_repaired_as_its_section(self, tmp_path):
        phone_skill(tmp_path)
        agent = make_agent(tmp_path, [*followed(worked=False), assistant_text(section())])
        on_phone(agent, "Android phone")
        learner = learner_after(agent, "how charged is my phone")
        assert learner.last_counted == [("check-battery", False)]
        offer = learner.consider()
        assert offer is not None, learner.last_skip
        sent = agent.provider.last_request()["messages"][0].text()
        assert "and a step failed. The task was then finished another way" in sent
        assert "THIS RUN WAS ON Android phone" in sent
        assert offer.repairs.name == "check-battery"

    def test_a_skill_not_taught_on_a_phone_is_left_alone(self, tmp_path):
        phone_skill(tmp_path, "1. Open Settings.\n2. Tap Battery.\n")
        agent = make_agent(tmp_path, followed())
        on_phone(agent, "Android phone")
        learner = learner_after(agent, "how charged is my phone")
        assert learner.other_phone is None
