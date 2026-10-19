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

const OTHER_LEAGUES: [string, string][] = [
  ["la_liga", "La Liga"],
  ["serie_a", "Serie A"],
  ["bundesliga", "Bundesliga"],
  ["ligue_1", "Ligue 1"],
  ["eredivisie", "Eredivisie"],
];

export default function MethodsPage() {
  const pred = readBacktest<J>("predictor");
  const val = readBacktest<J>("valuation");
  const real = readBacktest<J>("realism");
  const rum = readBacktest<J>("rumours");
  const rum6 = rum?.horizons?.["6"];
  const inj = readBacktest<J>("injury");
  const lin = readBacktest<J>("lineup");
  const linOther = readBacktest<J>("lineup_recent");
  // each league's own forecast backtest: the stack is published where it beat Elo on every score in both seasons
  const others = OTHER_LEAGUES.map(([key, name]) => {
    const j = readBacktest<J>(`predictor_${key}`);
    const stack = !!j && Object.values(j.beats_elo as Record<string, Record<string, boolean>>).every((v) => Object.values(v).every(Boolean));
    return { key, name, j, stack };
  });
  const opp = readBacktest<J>("opportunity");
  const pot = readBacktest<J>("potential");
  const rat = readBacktest<J>("player_ratings");
  const roles = readBacktest<J>("roles");
  const fp = readBacktest<J>("fingerprint");
  const nli = readBacktest<J>("factcheck_nli");
  const hold = readBacktest<J>("agents_eval_templates_holdout");
  const holdLlm = readBacktest<J>("agents_eval_llm_holdout");
  const wft = readBacktest<J>("writer_finetune");
  const wm = wft?.models ?? {};
  const [ft, ftBase, fallback, hosted] = [
    wm["local:dugout-writer-1.5b"],
    wm["local:qwen2.5-1.5b-8k"],
    wm["local:qwen2.5:3b"],
    wm["groq:qwen/qwen3.8-27b"],
  ];
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
          <Row name="Forecasts in other leagues" verdict="the stack where it wins · Elo elsewhere">
            Same replay, each league&apos;s own history (no expected goals outside the Premier League, so the stack and
            Elo come out almost level). Ranked probability score, last season:
            {others.map(({ key, name, j, stack }) => (
              <span key={key} className="block font-mono text-sm">
                <span data-name>{name}</span>: stack <B file={`predictor_${key}`} j={j} value={j?.results?.["2025-26"]?.stack_pre_lineup?.rps} label={`RPS ${name} stack`} /> · Elo{" "}
                <B file={`predictor_${key}`} j={j} value={j?.results?.["2025-26"]?.elo?.rps} label={`RPS ${name} Elo`} /> · closing odds{" "}
                <B file={`predictor_${key}`} j={j} value={j?.results?.["2025-26"]?.bookmaker_closing?.rps} label={`RPS ${name} bookmaker`} /> · shown: {stack ? "stack" : "Elo"}
              </span>
            ))}
          </Row>
          <Row name="Valuation" verdict="beats market value">
            Gradient boosting on past fees. Median error on test transfers{" "}
            <B file="valuation" j={val} value={val?.model_on_baseline_rows?.median_ape} label="median error, model" fmt="pct" /> vs{" "}
            <B file="valuation" j={val} value={val?.baseline_market_value?.median_ape} label="median error, market value" fmt="pct" /> for
            &ldquo;fee = market value&rdquo;.
            {val?.ranges && (
              <>
                {" "}
                Every price comes with a likely range, wider for cheap players, whose fees vary most: on test transfers{" "}
                <B file="valuation" j={val} value={val.ranges.segments?.all?.coverage} label="fees inside the likely range" fmt="pct" />{" "}
                of fees landed inside it (aim:{" "}
                <B file="valuation" j={val} value={val.ranges.level} label="share of fees the range aims to hold" fmt="pct" />
                ).
              </>
            )}
          </Row>
          <Row name="Transfer realism" verdict="calibrated parts">
            P(sold in a window) × the club&apos;s share of plausible buyers. Sale model AUC{" "}
            <B file="realism" j={real} value={real?.mobility?.auc} label="mobility AUC" />; destination AUC{" "}
            <B file="realism" j={real} value={real?.destination?.auc} label="destination AUC" />.
          </Row>
          <Row name="Rumour track record" verdict="beats the base rate · outlets barely differ">
            What became of every rumour in the Guardian&apos;s Rumour Mill archive: each one checked against the move
            that did or did not happen within half a year. On the latest season Brier{" "}
            <B file="rumours" j={rum} value={rum6?.model?.brier} label="Brier, rumour track record" /> vs{" "}
            <B file="rumours" j={rum} value={rum6?.recent_rate?.brier} label="Brier, recent base rate" /> for the recent base rate; AUC{" "}
            <B file="rumours" j={rum} value={rum6?.model?.auc} label="AUC, rumour track record" />. Outlets&apos; records barely differ once
            shrunk to the average, so a rumour is judged on its own signs: repeat reports, how many clubs it names, the window.{" "}
            <a className="underline" href="/rumours/">The record by outlet and season</a>.
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
            {inj?.leagues && (
              <>
                {" "}
                Other leagues have no current minutes, so their tiers come from the injury record and age alone:{" "}
                {Object.entries(inj.leagues as J)
                  .filter(([k, r]) => k !== "premier_league" && r.scored)
                  .map(([k, r], i) => (
                    <span key={k}>
                      {i > 0 && ", "}
                      <span data-name>{r.name}</span> AUC{" "}
                      <B file="injury" j={inj} value={r.results?.history_pooled?.auc} label={`injury AUC, ${r.name}`} />
                    </span>
                  ))}
                {(inj.unscored_leagues as string[]).length > 0 && (
                  <>
                    ; not rated in{" "}
                    {(inj.unscored_leagues as string[]).map((n, i) => (
                      <span key={n}>
                        {i > 0 && ", "}
                        <span data-name>{n}</span>
                      </span>
                    ))}{" "}
                    (too little injury data)
                  </>
                )}
                .
              </>
            )}
          </Row>
          <Row name="Lineup optimiser" verdict="slightly beats heuristic">
            CP-SAT integer programme. Predicted XI overlap with the real XI{" "}
            <B file="lineup" j={lin} value={lin ? (Object.values(lin.optimiser_overlap as Record<string, number>).at(-1) as number) : undefined} label="XI overlap" fmt="pct" /> vs{" "}
            <B file="lineup" j={lin} value={lin?.recent_minutes_baseline_overlap} label="XI overlap, heuristic" fmt="pct" /> for recent minutes.
          </Row>
          <Row name="Predicted XI in other leagues" verdict="the last XI, absentees replaced">
            Without FPL minutes, the best rule found is the club&apos;s last league XI (recency-weighted starts did not
            beat it). Share of the real XI predicted last season{" "}
            <B file="lineup_recent" j={linOther} value={linOther?.test?.["2025"]?.last_xi_overlap} label="XI overlap, last XI" fmt="pct" />; players
            API-Football lists as missing are replaced by the most recent other starter.
          </Row>
          <Row name="Player quality (VAEP)" verdict="beats base rate">
            Action values on open event data. Scoring-model AUC <B file="player_ratings" j={rat} value={rat?.vaep_holdout?.scores?.auc} label="VAEP AUC" />.
          </Row>
          <Row name="Roles & similarity" verdict="stable roles">
            Gaussian mixtures on <span data-meta>per-90</span> profiles. Role stability (adjusted Rand){" "}
            <B file="roles" j={roles} value={roles?.role_stability_ari} label="role stability ARI" />.
          </Row>
          {ft && (
            <Row
              name="Fine-tuned writer"
              verdict={fallback && ft.accepted > fallback.accepted ? "beats the local fallback" : "does not beat the local fallback"}
            >
              A small open model (<span data-name>Qwen2.5-1.5B</span>, LoRA) trained on{" "}
              <B file="writer_finetune" j={wft} value={wft.dataset?.examples?.train} label="training notes" fmt="int" /> notes that
              passed this gate, written by the larger open model dugout uses on Groq. On clubs it never saw, its notes passed every
              check <B file="writer_finetune" j={wft} value={ft.accepted} label="accepted, fine-tuned" fmt="pct" /> of the time vs{" "}
              <B file="writer_finetune" j={wft} value={ftBase?.accepted} label="accepted, base model" fmt="pct" /> before fine-tuning,{" "}
              <B file="writer_finetune" j={wft} value={fallback?.accepted} label="accepted, local fallback" fmt="pct" /> for the
              current local fallback and{" "}
              <B file="writer_finetune" j={wft} value={hosted?.accepted} label="accepted, hosted writer" fmt="pct" /> for the hosted
              writer. Drafts that tried to write a number:{" "}
              <B file="writer_finetune" j={wft} value={ft.stray_number_drafts} label="stray-number drafts, fine-tuned" fmt="int" /> vs{" "}
              <B file="writer_finetune" j={wft} value={ftBase?.stray_number_drafts} label="stray-number drafts, base model" fmt="int" />.
            </Row>
          )}
        </ul>
        <p className="mono-label mt-6">
          Full reports, including what did not work (FootballBERT embeddings were not adopted: the signal was leakage):{" "}
          <a className="underline" href={`${REPO_URL}/tree/main/docs/backtests`}>docs/backtests</a>
        </p>
      </section>
    </article>
  );
}
