"""earnings-desk: Streamlit entry point."""
import streamlit as st

st.set_page_config(page_title="earnings-desk", page_icon="📑", layout="wide")

nav = st.navigation([
    st.Page("pages/company.py", title="Company numbers", icon="📊", default=True),
])
st.sidebar.caption("Every number comes from SEC filings. No investment advice.")
nav.run()
