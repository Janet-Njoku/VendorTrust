"use client";

import type { AssessmentDetail, AssessmentResult } from "@/lib/api/types";
import { BUCKET_LABEL, contribution, formatDateTime, titleCase } from "@/lib/format";

import { CategoryBars, ScoreGauge } from "./charts";
import { Card, RiskBadge } from "./ui";

/**
 * A scored assessment: the score and level, the category scores, and a weighted breakdown that shows
 * how many score points each question contributed (weight x risk points / applicable weight), so the
 * contributions add up to the overall score.
 */
export function Results({ result, detail }: { result: AssessmentResult; detail: AssessmentDetail }) {
  const { breakdown } = result;
  const byQuestion = new Map(detail.questions.map((q) => [q.id, q]));
  const rows = breakdown.items.map((item) => {
    const question = byQuestion.get(item.question_id);
    const chosen = question?.options.find((o) => o.id === question.response?.option_id);
    return {
      item,
      prompt: question?.prompt ?? "Question",
      answer: chosen?.label ?? "—",
      points: contribution(item.weight, item.risk_points, breakdown.applicable_weight),
    };
  });
  const worstFirst = [...rows].sort((a, b) => b.points - a.points);

  return (
    <div className="space-y-6">
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Overall risk score" className="lg:col-span-1">
          <ScoreGauge score={result.score} level={result.risk_level} thresholds={breakdown.thresholds} />
          <div className="mt-3 flex flex-col items-center gap-1">
            <RiskBadge level={result.risk_level} />
            <p className="text-xs text-slate-500">
              Scored {formatDateTime(result.scored_at)} · method {breakdown.method}
            </p>
          </div>
        </Card>
        <Card title="Score by category" className="lg:col-span-2">
          <CategoryBars categories={breakdown.categories} thresholds={breakdown.thresholds} />
          <p className="mt-4 text-xs text-slate-500">
            Each bar is the weighted average risk of that category&apos;s applicable questions (0 is best, 100
            is worst).
          </p>
        </Card>
      </div>

      <Card title="Weighted breakdown">
        <p className="mb-4 text-sm text-slate-600">
          Overall score = sum of (weight × risk points) ÷ total applicable weight ({breakdown.applicable_weight}).
          Questions answered &ldquo;not applicable&rdquo; are excluded. The largest contributors are listed first.
        </p>
        <div className="overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-100 text-sm">
            <caption className="sr-only">Contribution of each question to the score</caption>
            <thead className="text-left text-xs uppercase tracking-wide text-slate-500">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">Question</th>
                <th scope="col" className="py-2 pr-4 font-medium">Answer</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Weight</th>
                <th scope="col" className="py-2 pr-4 text-right font-medium">Risk pts</th>
                <th scope="col" className="py-2 text-right font-medium">Adds</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {worstFirst.map(({ item, prompt, answer, points }) => (
                <tr key={item.question_id} className={item.risk_points === null ? "text-slate-400" : undefined}>
                  <td className="max-w-md py-2.5 pr-4">
                    <span className="block text-xs uppercase tracking-wide text-slate-500">{titleCase(item.category)}</span>
                    {prompt}
                  </td>
                  <td className="py-2.5 pr-4">{answer}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{item.weight}</td>
                  <td className="py-2.5 pr-4 text-right tabular-nums">{item.risk_points ?? "N/A"}</td>
                  <td className="py-2.5 text-right font-medium tabular-nums">
                    {item.risk_points === null ? "excluded" : points.toFixed(1)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-xs text-slate-500">
          Thresholds frozen with this questionnaire version: Low below {breakdown.thresholds.low_max}, Medium below{" "}
          {breakdown.thresholds.medium_max}, High below {breakdown.thresholds.high_max}.{" "}
          {BUCKET_LABEL[result.risk_level]} applies at {result.score.toFixed(1)}. Scores reflect self-reported
          answers.
        </p>
      </Card>
    </div>
  );
}
