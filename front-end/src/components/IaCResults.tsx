import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";

export interface IaCResults {
  results: string[] | null;
}

export default function IaCResults({ results }: IaCResults) {
  return (
    <div className="px-14 overflow-x-auto font-main pb-4 w-screen">
      <div className="text-2xl font-bold pb-2 text-blue">IaC Results</div>
      {results ? (
        <div className='min-w-full'>
          {results.map((code, index) => (
            <SyntaxHighlighter
              language="hcl"
              style={vscDarkPlus}
              key={index}
              customStyle={{ borderRadius: "10px", padding: "20px", fontSize: "16px" }}
            >
              {code}
            </SyntaxHighlighter>
          ))}
        </div>
      ) : (
        <div>No results found.</div>
      )}
    </div>
  );
}
