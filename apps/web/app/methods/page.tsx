import Num from "@/components/Num";
import { readBacktest, REPO_URL, type Stat } from "@/lib/data";

export const metadata = { title: "How it works — dugout" };

type J = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** A backtest score as a hoverable number: source = the report file, timestamp = when it was generated. */
function B({ file, j, value, label, fmt = "f3" }: { file: string; j: J | null; value: number | undefined; label: string; fmt?: "f3" | "pct" | "int" | "x" }) {
  if (!j || value === undefined || value === null) return <span className="text-pencil">n/a</span>;
  const display =
    fmt === "pct" ? `${(value * 100).toFixed(0)}%` : fmt === "int" ? String(Math.round(value)) : fmt === "x" ? `${value.toFixed(1)}×` : value.toFixed(3);
  const stat: Stat = {
    value,
    display,
    unit: null,
    metric: label,
    entity_type: "backtest",
    entity_id: file,
    entity_name: `docs/backtests/${file}.json`,
    source: "dugout backtest",
    source_url: `${REPO_URL}/blob/main/docs/backtests/${file}.md`,
    source_tier: 3,
    fetched_at: j.generated_at,
    query_kind: "backtest",
    status: "model_output",
    cross_checks: [],
  };
  return <Num stat={stat} display={display} />;
}

function Row({ name, children, verdict }: { name: string; children: React.ReactNode; verdict: string }) {
  return (
    <li className="ruled grid grid-cols-12 gap-3 py-5">
      <h3 className="col-span-12 font-display text-3xl leading-none sm:col-span-4">{name}</h3>
      <div className="col-span-12 sm:col-span-6">{children}</div>
      <p className="mono-label col-span-12 sm:col-span-2 sm:text-right">{verdict}</p>
    </li>
  );
}

export default function MethodsPage() {
  const pred = readBacktest<J>("predictor");
  const val = readBacktest<J>("valuation");
  const real = readBacktest<J>("realism");
  const inj = readBacktest<J>("injury");
  const lin = readBacktest<J>("lineup");
  const opp = readBacktest<J>("opportunity");
  const pot = readBacktest<J>("potential");
  const rat = readBacktest<J>("player_ratings");
  const roles = readBacktest<J>("roles");
  const fp = readBacktest<J>("fingerprint");
  const nli = readBacktest<J>("factcheck_nli");
  const hold = readBacktest<J>("agents_eval_templates_holdout");
  const holdLlm = readBacktest<J>("agents_eval_llm_holdout");
  const seasons = pred ? Object.keys(pred.results) : [];
  return (
    <article className="grid12 pt-10">
      <p className="hang mono-label">How it works · every forecast tested on past seasons · weak results included</p>
      <h1 className="hang mt-3 font-display text-[clamp(3rem,9vw,7rem)] leading-[0.9]">How dugout knows what it says.</h1>

      <section className="hang mt-14" aria-labelledby="rule">
        <h2 id="rule" className="mono-label">The rule</h2>
        <p className="mt-3 max-w-2xl font-display text-3xl leading-snug">
          AI writes the words, never the numbers. Every figure is filled straight from the data, and nothing is published
          unless two independent checks pass.
        </p>
        <ol className="mt-6 grid max-w-3xl gap-6 sm:grid-cols-2">
          <li>
            <p className="mono-label">Stat Auditor · no language model</p>
            <p className="mt-2">
              Every cited number&apos;s query is re-run against its source and must reproduce the stored value; goals, minutes,
              availability and fees are compared with a second source; any digit or number word not filled from the ledger fails
              the build.
            </p>
          </li>
          <li>
            <p className="mono-label">Fact-Check · <span data-meta>DeBERTa-v3</span> NLI + BGE retrieval</p>
            <p className="mt-2">
              Every other claim needs one official source or two independent ones (one vote per ownership group). Contradicted
              claims withhold the section; unsupported sentences are cut. False verifications on hand-labelled football claims:{" "}
              <B file="factcheck_nli" j={nli} value={nli?.results?.base?.false_verifications} label="false verifications" fmt="int" />.
            </p>
          </li>
        </ol>
        {hold && (
          <p className="mt-6 max-w-3xl">
            On held-out questions the system routed <B file="agents_eval_templates_holdout" j={hold} value={hold.summary.routing_accuracy} label="routing accuracy" fmt="pct" /> correctly,
            answered <B file="agents_eval_templates_holdout" j={hold} value={hold.summary.answer_recall} label="answer recall" fmt="pct" />,
            refused every unanswerable question, and published{" "}
            <B file="agents_eval_templates_holdout" j={hold} value={hold.summary.untraceable_number_rate} label="untraceable number rate" fmt="int" /> untraceable
            numbers. The open-weight LLM&apos;s drafts passed every check for{" "}
            <B file="agents_eval_llm_holdout" j={holdLlm} value={holdLlm ? holdLlm.summary.llm_sections / (holdLlm.summary.llm_sections + holdLlm.summary.template_sections) : undefined} label="share of sections the LLM wrote" fmt="pct" />{" "}
            of sections; deterministic templates wrote the rest.
          </p>
        )}
      </section>

      <section className="hang mt-16" aria-labelledby="models">
        <h2 id="models" className="mono-label">The models</h2>
        <ul className="mt-4">
          <Row name="Match predictor" verdict="beats Elo · bookmakers still better">
            Elo + expected-goals Dixon–Coles stack, refit every four gameweeks. Ranked probability score (lower is better):
            {seasons.map((s) => (
              <span key={s} className="block font-mono text-sm">
                <span data-meta>{s}</span>: dugout <B file="predictor" j={pred} value={pred?.results[s]?.stack_pre_lineup?.rps} label={`RPS ${s} dugout`} /> · Elo{" "}
                <B file="predictor" j={pred} value={pred?.results[s]?.elo?.rps} label={`RPS ${s} Elo`} /> · closing odds{" "}
                <B file="predictor" j={pred} value={pred?.results[s]?.bookmaker_closing?.rps} label={`RPS ${s} bookmaker`} />
              </span>
            ))}
          </Row>
          <Row name="Valuation" verdict="beats market value">
            Gradient boosting on past fees. Median error on test transfers{" "}
            <B file="valuation" j={val} value={val?.model_on_baseline_rows?.median_ape} label="median error, model" fmt="pct" /> vs{" "}
            <B file="valuation" j={val} value={val?.baseline_market_value?.median_ape} label="median error, market value" fmt="pct" /> for
            &ldquo;fee = market value&rdquo;.
          </Row>
          <Row name="Transfer realism" verdict="calibrated parts">
            P(sold in a window) × the club&apos;s share of plausible buyers. Sale model AUC{" "}
            <B file="realism" j={real} value={real?.mobility?.auc} label="mobility AUC" />; destination AUC{" "}
            <B file="realism" j={real} value={real?.destination?.auc} label="destination AUC" />.
          </Row>
          <Row name="Player potential" verdict="beats age curve · wide bands">
            Projected market value. Median error two years ahead{" "}
            <B file="potential" j={pot} value={pot?.horizons?.["2"]?.model?.median_ape} label="median error 2y" fmt="pct" /> (age curve{" "}
            <B file="potential" j={pot} value={pot?.horizons?.["2"]?.age_curve?.median_ape} label="median error 2y, age curve" fmt="pct" />); four years{" "}
            <B file="potential" j={pot} value={pot?.horizons?.["4"]?.model?.median_ape} label="median error 4y" fmt="pct" />.
          </Row>
          <Row name="Playing-time outlook" verdict="the validated part of fit">
            Expected minutes share at a new club. R² <B file="opportunity" j={opp} value={opp?.results?.opportunity_model?.r2} label="R²" /> vs{" "}
            <B file="opportunity" j={opp} value={opp?.results?.baseline_prev_share_value?.r2} label="R² baseline" /> for last season&apos;s minutes alone.
          </Row>
          <Row name="Style similarity" verdict="descriptive only">
            Manager fingerprint from Opta-derived team shares. It does not predict minutes (Spearman{" "}
            <B file="fingerprint" j={fp} value={fp?.spearman_fit_vs_minutes_share} label="Spearman fit vs minutes" />), so it is shown as description, never as a forecast.
          </Row>
          <Row name="Injury risk" verdict="tiers, not percentages">
            Load and history model. AUC <B file="injury" j={inj} value={inj?.results?.logistic?.auc} label="injury AUC" />, top-decile lift{" "}
            <B file="injury" j={inj} value={inj?.results?.logistic?.top_decile_lift} label="top-decile lift" fmt="x" />; shown as risk tiers because calibration is limited.
          </Row>
          <Row name="Lineup optimiser" verdict="slightly beats heuristic">
            CP-SAT integer programme. Predicted XI overlap with the real XI{" "}
            <B file="lineup" j={lin} value={lin ? (Object.values(lin.optimiser_overlap as Record<string, number>).at(-1) as number) : undefined} label="XI overlap" fmt="pct" /> vs{" "}
            <B file="lineup" j={lin} value={lin?.recent_minutes_baseline_overlap} label="XI overlap, heuristic" fmt="pct" /> for recent minutes.
          </Row>
          <Row name="Player quality (VAEP)" verdict="beats base rate">
            Action values on open event data. Scoring-model AUC <B file="player_ratings" j={rat} value={rat?.vaep_holdout?.scores?.auc} label="VAEP AUC" />.
          </Row>
          <Row name="Roles & similarity" verdict="stable roles">
            Gaussian mixtures on <span data-meta>per-90</span> profiles. Role stability (adjusted Rand){" "}
            <B file="roles" j={roles} value={roles?.role_stability_ari} label="role stability ARI" />.
          </Row>
        </ul>
        <p className="mono-label mt-6">
          Full reports, including what did not work (FootballBERT embeddings were not adopted: the signal was leakage):{" "}
          <a className="underline" href={`${REPO_URL}/tree/main/docs/backtests`}>docs/backtests</a>
        </p>
      </section>
    </article>
  );
}
