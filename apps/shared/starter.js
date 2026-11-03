async function load() {
  const status = document.querySelector('#status');
  try {
    const response = await fetch('/api/scenarios');
    if (!response.ok) throw new Error('Fixture server returned ' + response.status);
    const data = await response.json();
    if (data.mode !== 'fixture' || data.synthetic !== true)
      throw new Error('Expected explicitly synthetic fixtures');
    for (const item of data.items) {
      const card = document.createElement('article');
      const heading = document.createElement('h3');
      heading.textContent = item.id + ' · ' + item.name;
      const scenario = document.createElement('p');
      scenario.textContent = item.scenario.replaceAll('_', ' ');
      const outcome = document.createElement('small');
      outcome.textContent = 'Expected review outcome: ' + item.expected.replaceAll('_', ' ');
      card.append(heading, scenario, outcome);
      document.querySelector('#cases').append(card);
    }
    status.textContent =
      data.items.length + ' synthetic scenarios loaded. No live taxpayer records.';
  } catch (error) {
    status.textContent = 'Unable to load fixtures: ' + error.message;
  }
}
load();
