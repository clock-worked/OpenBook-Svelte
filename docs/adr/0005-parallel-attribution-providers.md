# Attribution providers are parallel, not replacements

Speaker-attribution backends (Gemini, local llama, JEV, Open-Jev) are parallel providers behind one interface in `py_services`. A new provider joins alongside the existing ones rather than replacing them, so each book can keep the pipeline that works for it, and experiments compare against the frozen baseline instead of a moving target.
