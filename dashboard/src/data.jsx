import {createContext, useContext, useEffect, useMemo, useState} from 'react';

const DataContext = createContext({status: 'loading', figures: {}, zones: null});

async function load(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

export function DataProvider({children}) {
  const [state, setState] = useState({status: 'loading', figures: {}, zones: null});

  useEffect(() => {
    let live = true;
    Promise.all([load('data/evidence.json'), load('data/zones.json').catch(() => null)])
      .then(([catalogue, zones]) => {
        if (!live) return;
        setState({status: 'ready', figures: Object.fromEntries(catalogue.map(item => [item.id, item])), zones});
      })
      .catch(error => live && setState({status: 'error', figures: {}, zones: null, error: String(error)}));
    return () => { live = false; };
  }, []);

  return <DataContext.Provider value={state}>{children}</DataContext.Provider>;
}

export const useData = () => useContext(DataContext);

export function useFigure(id) {
  const {figures, status} = useData();
  const figure = figures[id];
  const rows = useMemo(() => {
    if (!figure?.rows) return null;
    return figure.rows.map(row => Object.fromEntries(figure.columns.map((column, index) => [column, row[index]])));
  }, [figure]);
  return {status, figure, rows};
}
