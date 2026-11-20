export default function Icon({ name, className = '' }) {
  return (
    <svg className={className} width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {name === 'code' && <><path d="m8 5-7 7 7 7" /><path d="m16 5 7 7-7 7" /></>}
      {name === 'code-slash' && <><path d="m7 6-6 6 6 6m10-12 6 6-6 6M14 3l-4 18" /></>}
      {name === 'briefcase' && <><rect x="3" y="7" width="18" height="14" rx="1.5" /><path d="M8 7V4h8v3M8 7v14M16 7v14" /></>}
      {name === 'graduation' && <><path d="m2 9 10-5 10 5-10 5-10-5ZM6 11v6c4 3 8 3 12 0v-6M22 9v7" /></>}
      {name === 'arrow' && <path d="m9 7 5 5-5 5" />}
      {name === 'back' && <path d="M20 12H4m6-6-6 6 6 6" />}
      {name === 'home' && <><path d="m3 10 9-7 9 7v11H3V10Z" /><path d="M9 21v-9h6v9" /></>}
      {name === 'results' && <><path d="M2 21h20M5 21V11h4v10m0-14h5v14m0-19h5v19" /></>}
      {name === 'bell' && <><path d="M4 16v-5a8 8 0 0 1 16 0v5l2 3H2l2-3ZM9 22h6" /></>}
      {name === 'drafts' && <><rect x="4" y="5" width="16" height="17" rx="2" /><path d="M9 5V2h11v14M8 11h8m-8 4h5" /></>}
      {name === 'profile' && <><circle cx="12" cy="6" r="4" /><path d="M5 22v-3a7 7 0 0 1 14 0v3" /></>}
      {name === 'sections' && <><rect x="3" y="3" width="7" height="7" rx="2" /><rect x="14" y="3" width="7" height="7" rx="2" /><rect x="3" y="14" width="7" height="7" rx="2" /><rect x="14" y="14" width="7" height="7" rx="2" /></>}
      {name === 'eye' && <><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" /></>}
      {name === 'eye-off' && <><path d="m3 3 18 18M10 5.2A12 12 0 0 1 12 5c7 0 10 7 10 7a18 18 0 0 1-3 4M6 6C3.3 8.2 2 12 2 12s3 7 10 7a11 11 0 0 0 5-1.2M10 10a3 3 0 0 0 4 4" /></>}
    </svg>
  )
}
