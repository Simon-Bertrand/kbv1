import React, { memo } from "react";

const AthenaView = memo(function AthenaView() {
  return (
    <>
      <section id="athena" className="view active">
        <div className="page-heading">
          <div>
            <h1>{"Agent"}</h1>
          </div>
        </div>
        <div className="ask-card">
          <form id="askForm">
            <label className="sr" htmlFor="question">
              {"Question documentaire"}
            </label>
            <textarea
              id="question"
              maxLength="4000"
              required={true}
              placeholder="Quelle information recherchez-vous ?"
              defaultValue=""
            ></textarea>
            <div className="form-bottom">
              <label>
                {"Recherche "}
                <select id="askMode" defaultValue="hybrid">
                  <option value="hybrid">{"Hybride · BM25 + E5"}</option>
                  <option value="lexical">{"Lexicale · BM25"}</option>
                  <option value="semantic">{"Sémantique · E5"}</option>
                </select>
              </label>
              <label>
                {"Durée maximale "}
                <select id="budget" defaultValue="0">
                  <option value="0">Sans limite de temps</option>
                  <option value="240">{"4 minutes"}</option>
                  <option value="600">{"10 minutes"}</option>
                  <option value="900">{"15 minutes"}</option>
                </select>
              </label>
              <button className="primary" id="askButton">
                {"Rechercher "}

              </button>
            </div>
          </form>
        </div>
        <div id="askNotice" role="status" className="notice hidden"></div>
        <article id="answerCard" className="answer-card hidden">
          <div className="card-top">
            {"RÉPONSE "}
            <button id="answerTrace" className="text-button">
              {"Voir dans le Suivi"}
            </button>
          </div>
          <div id="answerText"></div>
          <div id="answerSources" className="answer-sources"></div>
          <div id="answerWarnings"></div>
        </article>
        <div className="section-heading">
          <h2>{"Dernières requêtes"}</h2>
          <button className="text-button" data-go="traces">
            {"Voir le Suivi"}
          </button>
        </div>
        <div id="recentRuns" className="recent-grid">
          <div className="empty">{"Aucune requête."}</div>
        </div>
      </section>
    </>
  );
});
export default AthenaView;
