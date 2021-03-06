ASKE-E Month 2 Milestone Report
===============================

Push science: EMMAA models tweet new discoveries and explanations
-----------------------------------------------------------------

This month we implemented and deployed Twitter integration for multiple
EMMAA models. We have previously developed a proof of concept for Twitter
integration, however, that framework had significant limitations. First,
tweets only described structural updates to a model (i.e., the number of
new statements that were added) and did not report on any functional changes
or non-trivial new insights that were gained from the model update.
Second, the tweets did not point to any landing page where users could
examine the specific changes to the model. In the new Twitter integration
framework, we addressed both of these crucial limitations.

Twitter updates are now generated for three distinct types of events triggered
by the appearance of new discoveries in the literature:

- New (note that "new" here means that a statement is meaningfully distinct
  from any other statement that the model previously contained) statements
  added to a model.
- The model becoming structurally capable to make a set of new explanations
  with respect to a set of tests (e.g., experimental findings). This typically
  happens if a new entity is added to the model that was previously not
  part of it.
- The model explaining a previously unexplained observation (in other words,
  passing a previously failing "test"). These notifications are particularly
  important conceptually, since they indicate that the model learned
  something from the newly ingested literature that changed it such that
  it could explain something it previously couldn't.

The image below shows the first tweet from the
[`EMMAA COVID-19 model`](https://twitter.com/covid19_emmaa).

.. image:: ../_static/images/covid19_twitter.png
    :scale: 75%
    :align: center

Crucially, each of the tweets above include a link to a specific landing page
where the new results can be examined and curated (in case there are any
issues).

Overall, this framework constitutes a new paradigm for scientists to monitor
the evolving literature around a given scientific topic. For instance,
scientists who follow the EMMAA COVID-19 model Twitter account get
targeted updates on specific new pieces of knowledge that were just published
that enable new explanations of drug-virus effects.

Improving named entity recognition in text mining integrated with EMMAA models
------------------------------------------------------------------------------

Having evaluated the performance of integrating protein cleavage product
names from the Protein Ontology with the Reach reading system's resources,
we found that the space of protein fragments covered and the quality
of synonyms was insufficient. We therefore implemented an alternative
approach that involves extracting protein chain and fragment names from
UniProt and using these as synonyms for grounding purposes
(see [`Pull request`](https://github.com/clulab/bioresources/pull/42)).
We found that this approach adds around 50 thousand new, high-quality
lexicalizations for protein fragments, including a large number of human
proteins (e.g., Angiotensin-2) and viral proteins (e.g., Nsp1) that are
of interest for COVID-19 and many other applications in biology. The UA
team is currently working on finalizing these updates and we hope to run an
updated version of Reach on the COVID-19 literature next month.

Making model tests and paths available for use by other applications
--------------------------------------------------------------------

To facilitate integration of EMMAA test results with other applications we made
data on model tests and causal paths available for programmatic download. This
feature was requested by the Uncharted team, who is exploring approaches to
visualize and interact with EMMAA results. The test and path data are stored in
public JSON-L files on Amazon S3 and are updated daily. Model test files
contain a JSON representation of the EMMAA test statements; test path files
list the path nodes, the statement hashes supporting each edge in the path, the
hash of the corresponding test, and the type of causal network used to evaluate
the test. Downstream applications can get the latest results from each
model-test corpus pair from stable Amazon S3 links.

