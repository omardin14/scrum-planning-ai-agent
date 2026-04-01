"""Tests for team calibration injection in story_writer and sprint_planner prompts."""

from __future__ import annotations


class TestStoryWriterCalibration:
    def test_calibration_injected_when_present(self):
        from scrum_agent.prompts.story_writer import get_story_writer_prompt

        calibration = "## Team Calibration Data\n- 5 pt: avg 4.2 day cycle time"
        prompt = get_story_writer_prompt(
            project_name="Test",
            project_description="A test project",
            project_type="greenfield",
            goals="- Build it",
            end_users="- developers",
            tech_stack="- Python",
            constraints="- none",
            features_block="F1: Feature One\n  Description",
            team_calibration=calibration,
        )
        assert "Team Calibration Data" in prompt
        assert "4.2 day cycle time" in prompt

    def test_no_calibration_section_when_empty(self):
        from scrum_agent.prompts.story_writer import get_story_writer_prompt

        prompt = get_story_writer_prompt(
            project_name="Test",
            project_description="A test project",
            project_type="greenfield",
            goals="- Build it",
            end_users="- developers",
            tech_stack="- Python",
            constraints="- none",
            features_block="F1: Feature One\n  Description",
            team_calibration="",
        )
        assert "Team Calibration Data" not in prompt

    def test_calibration_appears_before_task(self):
        from scrum_agent.prompts.story_writer import get_story_writer_prompt

        calibration = "## Team Calibration Data\n- 3 pt: avg 2.1 days"
        prompt = get_story_writer_prompt(
            project_name="Test",
            project_description="desc",
            project_type="greenfield",
            goals="",
            end_users="",
            tech_stack="",
            constraints="",
            features_block="F1",
            team_calibration=calibration,
        )
        cal_pos = prompt.find("Team Calibration Data")
        task_pos = prompt.find("## Task")
        assert cal_pos < task_pos


class TestSprintPlannerCalibration:
    def test_calibration_injected(self):
        from scrum_agent.prompts.sprint_planner import get_sprint_planner_prompt

        calibration = "## Team Calibration Data\nVelocity: 20 ± 3 pts/sprint"
        prompt = get_sprint_planner_prompt(
            project_name="Test",
            project_description="desc",
            velocity=20,
            target_sprints=3,
            stories_block="US-F1-001: 5 pts",
            team_calibration=calibration,
        )
        assert "Team Calibration Data" in prompt

    def test_no_calibration_when_empty(self):
        from scrum_agent.prompts.sprint_planner import get_sprint_planner_prompt

        prompt = get_sprint_planner_prompt(
            project_name="Test",
            project_description="desc",
            velocity=20,
            target_sprints=3,
            stories_block="US-F1-001: 5 pts",
            team_calibration="",
        )
        assert "Team Calibration Data" not in prompt


class TestAnalyzerCalibration:
    def test_team_profile_summary_injected(self):
        from scrum_agent.prompts.analyzer import get_analyzer_prompt

        summary = "Velocity: 22 pts/sprint\nEstimation accuracy: 80%"
        prompt = get_analyzer_prompt(
            answers_block="Q1: Build a todo app",
            team_size=4,
            velocity_per_sprint=20,
            team_profile_summary=summary,
        )
        assert "Team Historical Profile" in prompt
        assert "Velocity: 22" in prompt

    def test_no_team_section_when_empty(self):
        from scrum_agent.prompts.analyzer import get_analyzer_prompt

        prompt = get_analyzer_prompt(
            answers_block="Q1: Build a todo app",
            team_size=4,
            velocity_per_sprint=20,
            team_profile_summary="",
        )
        assert "Team Historical Profile" not in prompt
