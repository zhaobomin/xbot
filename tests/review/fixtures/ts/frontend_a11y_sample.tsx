function Good(): JSX.Element {
  return <img src="x" alt="desc" />; // clean: has alt
}

function Bad(): JSX.Element {
  return <img src="x" />; // anti: img without alt
}
