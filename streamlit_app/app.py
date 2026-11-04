"""earnings-desk: Streamlit entry point."""
import streamlit as st

st.set_page_config(page_title="earnings-desk", page_icon="📑", layout="wide")

nav = st.navigation([
    st.Page("pages/home.py", title="Is AI eating software?", icon="🏠", default=True),
    st.Page("pages/note.py", title="Verified note", icon="✅"),
    st.Page("pages/company.py", title="Company numbers", icon="📊"),
    st.Page("pages/scorecard.py", title="Scorecard", icon="🎯"),
])
st.sidebar.caption("Every number comes from SEC filings. No investment advice.")
nav.run()
