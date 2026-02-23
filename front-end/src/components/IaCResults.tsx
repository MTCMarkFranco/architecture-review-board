import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

export interface IaCResults {
  results: string[] | null;
}

export default function IaCResults({ results }: IaCResults) {
  return (
    <div className="overflow-x-auto font-main">
      <div className="text-xl font-semibold pb-3 text-ms-text">IaC Results</div>
      {results ? (
        <div className='min-w-full flex flex-col gap-y-3'>
          {results.map((code, index) => (
            <SyntaxHighlighter
              language="hcl"
              style={vscDarkPlus}
              key={index}
              customStyle={{ borderRadius: "8px", padding: "20px", fontSize: "14px" }}
            >
              {code}
            </SyntaxHighlighter>
          ))}
        </div>
      ) : (
        <div className="text-ms-text-secondary">No results found.</div>
      )}
    </div>
  );
}
