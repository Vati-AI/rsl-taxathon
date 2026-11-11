/**
 * Open-source notices (TAX-150): the components Vati Workspace is built on, with the
 * version this repository pins and the licence each upstream declares. This file and the
 * About · Open-source notices page are the only user-facing places that name components
 * (docs/DESIGN_STANDARD.md register); the naming check exempts this file.
 *
 * Versions come from integration/upstreams.lock.json, integration/services.json,
 * integration/frappe-framework.lock.json, requirements-dev.lock and package.json.
 * Licences were read from each upstream repository's licence file on 2026-11-11.
 */

export interface Notice {
  component: string;
  /** Where users meet it in Vati Workspace (the register name). */
  shownAs: string;
  version: string;
  licence: string;
  licenceUrl: string;
  source: string;
}

export const SERVICE_NOTICES: Notice[] = [
  {
    component: 'Jube',
    shownAs: 'Vati Workspace · Investigation',
    version: 'commit 01c4afd',
    licence: 'AGPL-3.0',
    licenceUrl: 'https://www.gnu.org/licenses/agpl-3.0.html',
    source: 'https://github.com/jube-home/aml-fraud-transaction-monitoring',
  },
  {
    component: 'OpenAleph',
    shownAs: 'Vati Workspace · Explore',
    version: '5.4.0-rc1 (commit f89ff18)',
    licence: 'MIT',
    licenceUrl: 'https://github.com/openaleph/openaleph/blob/main/LICENSE.txt',
    source: 'https://github.com/openaleph/openaleph',
  },
  {
    component: 'Frappe Framework',
    shownAs: 'Vati Workspace · Admin, Vati Taxpayer Portal and Vati records',
    version: 'v16.54.0',
    licence: 'MIT',
    licenceUrl: 'https://github.com/frappe/frappe/blob/develop/LICENSE',
    source: 'https://github.com/frappe/frappe',
  },
  {
    component: 'Docling Serve',
    shownAs: 'Document reading',
    version: '1.38.2',
    licence: 'MIT',
    licenceUrl: 'https://github.com/docling-project/docling-serve/blob/main/LICENSE',
    source: 'https://github.com/docling-project/docling-serve',
  },
  {
    component: 'yente',
    shownAs: 'Name screening',
    version: '5.5.0',
    licence: 'MIT',
    licenceUrl: 'https://github.com/opensanctions/yente/blob/main/LICENSE',
    source: 'https://github.com/opensanctions/yente',
  },
];

export const LIBRARY_NOTICES: Notice[] = [
  {
    component: 'LangGraph',
    shownAs: 'Vati assistant',
    version: '1.2.19',
    licence: 'MIT',
    licenceUrl: 'https://github.com/langchain-ai/langgraph/blob/main/LICENSE',
    source: 'https://github.com/langchain-ai/langgraph',
  },
  {
    component: 'FollowTheMoney (schema subset)',
    shownAs: 'Relationships in a case',
    version: 'commit c8c457a',
    licence: 'MIT',
    licenceUrl: 'https://github.com/opensanctions/followthemoney/blob/main/LICENSE',
    source: 'https://github.com/opensanctions/followthemoney',
  },
  {
    component: 'Vue',
    shownAs: 'Every Vati Workspace screen',
    version: '3.5.44',
    licence: 'MIT',
    licenceUrl: 'https://github.com/vuejs/core/blob/main/LICENSE',
    source: 'https://github.com/vuejs/core',
  },
  {
    component: 'Vue Router',
    shownAs: 'Every Vati Workspace screen',
    version: '4.6.4',
    licence: 'MIT',
    licenceUrl: 'https://github.com/vuejs/router/blob/main/LICENSE',
    source: 'https://github.com/vuejs/router',
  },
  {
    component: 'frappe-ui',
    shownAs: 'Every Vati Workspace screen',
    version: '1.1.0',
    licence: 'MIT',
    licenceUrl: 'https://github.com/frappe/frappe-ui/blob/main/LICENSE',
    source: 'https://github.com/frappe/frappe-ui',
  },
  {
    component: 'Reka UI',
    shownAs: 'Menus, dialogs and drawers',
    version: '2.11.0',
    licence: 'MIT',
    licenceUrl: 'https://github.com/unovue/reka-ui/blob/v2/LICENSE',
    source: 'https://github.com/unovue/reka-ui',
  },
  {
    component: 'Tailwind CSS',
    shownAs: 'Every Vati Workspace screen',
    version: '3.4.19',
    licence: 'MIT',
    licenceUrl: 'https://github.com/tailwindlabs/tailwindcss/blob/v3.4.19/LICENSE',
    source: 'https://github.com/tailwindlabs/tailwindcss',
  },
  {
    component: 'Lucide icons',
    shownAs: 'Icons',
    version: '1.67.0',
    licence: 'ISC',
    licenceUrl: 'https://github.com/lucide-icons/lucide/blob/main/LICENSE',
    source: 'https://github.com/lucide-icons/lucide',
  },
];

/** Hosted services that are not open source but process data for a feature. */
export const HOSTED_NOTICES: { service: string; shownAs: string; note: string }[] = [
  {
    service: 'Groq (hosted model inference)',
    shownAs: 'Vati assistant',
    note: 'Used only where the hosted assistant is enabled; synthetic data only in this prototype.',
  },
];
