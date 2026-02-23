import { ValidationEntry } from "../data/types.ts";

export interface ValidationTableProps {
  validateResult: ValidationEntry[] | null;
}

export default function ValidationTable({
  validateResult,
}: ValidationTableProps) {
  return (
    <div className="overflow-x-auto font-main">
      <div className="text-xl font-semibold pb-3 text-ms-text">
        Validation Results
      </div>
      {validateResult ? (
        <table className="border-collapse text-left min-w-full text-sm">
          <thead>
            <tr className="text-ms-text">
              <th className="border border-ms-border px-4 py-2.5 bg-ms-header font-semibold">Type</th>
              <th className="border border-ms-border px-4 py-2.5 bg-ms-header font-semibold">Issue</th>
              <th className="border border-ms-border px-4 py-2.5 bg-ms-header font-semibold">
                Description
              </th>
              <th className="border border-ms-border px-4 py-2.5 bg-ms-header font-semibold">
                Principles
              </th>
            </tr>
          </thead>
          <tbody>
            {validateResult.map((entry, index) => (
              <tr key={index} className={index % 2 === 0 ? 'bg-ms-row' : 'bg-ms-row-alt'}>
                <td className="border border-ms-border px-4 py-2.5 text-ms-text">
                  {entry.Type}{" "}
                  {entry.Mandatory && (
                    <span className="text-ms-danger font-semibold">(Mandatory)</span>
                  )}
                </td>
                <td className="border border-ms-border px-4 py-2.5 text-ms-text">
                  {entry.Issue}
                </td>
                <td className="border border-ms-border px-4 py-2.5 text-ms-text">
                  {entry.Description}
                </td>
                <td className="border border-ms-border px-4 py-2.5 text-ms-text">
                  {entry.Principles}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="text-ms-text-secondary">No validation results to display.</p>
      )}
    </div>
  );
}
