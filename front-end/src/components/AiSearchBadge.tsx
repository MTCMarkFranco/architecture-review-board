export default function AiSearchBadge() {
  return (
    <div className="flex items-center gap-2 text-ms-text-secondary">
      <svg className="h-5 w-5" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
        <defs>
          <linearGradient id="ai-search-grad" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="#0078D4" />
            <stop offset="100%" stopColor="#5C2D91" />
          </linearGradient>
        </defs>
        <circle cx="7.5" cy="7.5" r="6" fill="none" stroke="url(#ai-search-grad)" strokeWidth="2" />
        <line x1="12" y1="12" x2="16.5" y2="16.5" stroke="url(#ai-search-grad)" strokeWidth="2" strokeLinecap="round" />
        <circle cx="7.5" cy="7.5" r="2" fill="url(#ai-search-grad)" opacity="0.5" />
      </svg>
      <span className="text-xs font-semibold tracking-wide uppercase">Powered by Microsoft Foundry IQ</span>
    </div>
  );
}
