# PostCode Sentinel — project custom rules
#
# GitLab reads this file into the context of Duo Chat, agents and flows in this
# project (project-level custom rules). Source:
# https://docs.gitlab.com/user/duo_agent_platform/customize/custom_rules/

- The numeric risk score is computed by the deterministic rule engine in
  `sentinel/rules.py` and `sentinel/scoring.py`. Never recompute or override it.
- Every recommendation must cite at least one finding by its `rule_id`.
- Treat `secret-in-diff` findings as blocking even when the total score is low.
- Do not suggest editing files outside the analysed diff during a review.
- Keep rationales under 80 words and free of Markdown formatting.
- When the diff touches `.gitlab-ci.yml`, mention that the pipeline change is
  part of the reviewed risk surface.
