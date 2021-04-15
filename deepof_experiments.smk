# @authors lucasmiranda42
# encoding: utf-8
# deepof_experiments

"""

Snakefile for data and imputation.
Execution: sbatch snakemake
Plot DAG: snakemake --snakefile deepof_experiments.smk --forceall --dag | dot -Tpdf > deepof_experiments_DAG.pdf
Plot rule graph: snakemake --snakefile deepof_experiments.smk --forceall --rulegraph | dot -Tpdf > deepof_experiments_RULEGRAPH.pdf

"""

import os

outpath = "/psycl/g/mpsstatgen/lucas/DLC/DLC_autoencoders/DeepOF/deepof/logs/"

losses = ["ELBO"]  # , "MMD", "ELBO+MMD"]
encodings = [6]  # [2, 4, 6, 8, 10, 12, 14, 16]
cluster_numbers = [25]  # [1, 5, 10, 15, 20, 25]
latent_reg = ["none"]  # ["none", "categorical", "variance", "categorical+variance"]
entropy_knn = [100]
next_sequence_pred_weights = [0.0, 0.15]
phenotype_pred_weights = [0.0]
rule_based_pred_weights = [0.0, 0.15]
input_types = ["coords"]
run = list(range(1, 11))


rule deepof_experiments:
    input:
        # Elliptical arena detection
        # "/psycl/g/mpsstatgen/lucas/DLC/DLC_autoencoders/DeepOF/deepof/supplementary_notebooks/recognise_elliptical_arena.ipynb",
        # Hyperparameter tuning
        # expand(
        #     os.path.join(
        #         outpath,
        #         "coarse_hyperparameter_tuning/trained_weights/GMVAE_loss={loss}_k={k}_encoding={enc}_final_weights.h5",
        #     ),
        #     loss=losses,
        #     k=cluster_numbers,
        #     enc=encodings,
        # ),
        # Train a variety of models
        expand(
            outpath + "train_models/trained_weights/"
            "GMVAE_input_type={input_type}_"
            "NextSeqPred={nspredweight}_"
            "PhenoPred={phenpredweight}_"
            "RuleBasedPred={rulesweight}_"
            "loss={loss}_"
            "encoding={encs}_"
            "k={k}_"
            "latreg={latreg}_"
            "entropyknn={entknn}_"
            "run={run}_"
            "final_weights.h5",
            input_type=input_types,
            loss=losses,
            encs=encodings,
            k=cluster_numbers,
            latreg=latent_reg,
            entknn=entropy_knn,
            nspredweight=next_sequence_pred_weights,
            phenpredweight=phenotype_pred_weights,
            rulesweight=rule_based_pred_weights,
            run=run,
        ),


rule elliptical_arena_detector:
    input:
        to_exec="/psycl/g/mpsstatgen/lucas/DLC/DLC_autoencoders/DeepOF/deepof/supplementary_notebooks/recognise_elliptical_arena_blank.ipynb",
    output:
        exec="/psycl/g/mpsstatgen/lucas/DLC/DLC_autoencoders/DeepOF/deepof/supplementary_notebooks/recognise_elliptical_arena.ipynb",
    shell:
        "papermill {input.to_exec} "
        "-p vid_path './supplementary_notebooks/' "
        "-p log_path './logs/' "
        "-p out_path './deepof/trained_models/' "
        "{output.exec}"


rule coarse_hyperparameter_tuning:
    input:
        data_path="/psycl/g/mpsstatgen/lucas/DLC/DLC_models/deepof_single_topview/",
    output:
        trained_models=os.path.join(
            outpath,
            "coarse_hyperparameter_tuning/trained_weights/GMVAE_loss={loss}_k={k}_encoding={enc}_final_weights.h5",
        ),
    shell:
        "pipenv run python -m deepof.train_model "
        "--train-path {input.data_path} "
        "--val-num 25 "
        "--components {wildcards.k} "
        "--input-type coords "
        "--next-sequence-prediction {wildcards.nspredweight} "
        "--phenotype-prediction {wildcards.phenpredweight} "
        "--rule-based-prediction {wildcards.rulesweight} "
        "--variational True "
        "--loss {wildcards.loss} "
        "--kl-warmup 5 "
        "--mmd-warmup 5 "
        "--encoding-size {wildcards.enc} "
        "--batch-size 256 "
        "--window-size 24 "
        "--window-step 12 "
        "--output-path {outpath}coarse_hyperparameter_tuning "
        "--hyperparameter-tuning hyperband "
        "--hpt-trials 1"


rule train_models:
    input:
        data_path=ancient(
            "/psycl/g/mpsstatgen/lucas/DLC/DLC_models/deepof_single_topview/"
        ),
    output:
        trained_models=outpath + "train_models/trained_weights/"
        "GMVAE_input_type={input_type}_"
        "NextSeqPred={nspredweight}_"
        "PhenoPred={phenpredweight}_"
        "RuleBasedPred={rulesweight}_"
        "loss={loss}_"
        "encoding={encs}_"
        "k={k}_"
        "latreg={latreg}_"
        "entropyknn={entknn}_"
        "run={run}_"
        "final_weights.h5",
    shell:
        "pipenv run python -m deepof.train_model "
        "--train-path {input.data_path} "
        "--val-num 15 "
        "--components {wildcards.k} "
        "--input-type {wildcards.input_type} "
        "--next-sequence-prediction {wildcards.nspredweight} "
        "--phenotype-prediction {wildcards.phenpredweight} "
        "--rule-based-prediction {wildcards.rulesweight} "
        "--variational True "
        "--latent-reg {wildcards.latreg} "
        "--loss {wildcards.loss} "
        "--kl-warmup 5 "
        "--mmd-warmup 5 "
        "--montecarlo-kl 10 "
        "--encoding-size {wildcards.encs} "
        "--entropy-knn {wildcards.entknn} "
        "--batch-size 256 "
        "--window-size 24 "
        "--window-step 12 "
        "--run {wildcards.run} "
        "--output-path {outpath}train_models"
