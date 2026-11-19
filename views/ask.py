import streamlit as st

from pricewise import assistant, db, gemini, ui

ui.header("Ask PriceWise", "Ask about your shopping in plain words. The answer's numbers are calculated from your "
          "receipts, never made up, and each answer shows what it's based on.")
if db.is_empty():
    ui.empty("No purchases yet, so there's nothing to ask about.")
    st.stop()

if "chat" not in st.session_state:
    st.session_state["chat"] = []

mode = "Gemini understands your wording" if gemini.enabled() else "Offline: works best with questions like the ones below"
st.caption(mode)
picked = st.pills("Try", assistant.SUGGESTIONS, label_visibility="collapsed", key=f"pills{len(st.session_state['chat'])}")
typed = st.chat_input("Ask about prices, stores, budgets, overcharges...")
question = typed or picked

for q, a in st.session_state["chat"]:
    with st.chat_message("user", avatar=":material/person:"):
        st.write(q)
    with st.chat_message("assistant", avatar=":material/sell:"):
        st.markdown(a.text)
        if a.table is not None and not a.table.empty:
            st.dataframe(a.table, hide_index=True, width="stretch")
        if a.evidence:
            st.caption("Based on: " + "; ".join(a.evidence) + f". Question understood by {a.routed_by}.")

if question:
    with st.spinner("Working it out from your receipts..."):
        ans = assistant.ask(question)
    st.session_state["chat"].append((question, ans))
    st.rerun()

if st.session_state["chat"] and st.button("Clear conversation"):
    st.session_state["chat"] = []
    st.rerun()
