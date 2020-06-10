# @author lucasmiranda42

from keras import backend as K
from tensorflow.keras.constraints import Constraint
from tensorflow.keras.layers import Layer
import tensorflow as tf
import tensorflow_probability as tfp

tfd = tfp.distributions
tfpl = tfp.layers

# Helper functions
def compute_kernel(x, y):
    x_size = K.shape(x)[0]
    y_size = K.shape(y)[0]
    dim = K.shape(x)[1]
    tiled_x = K.tile(K.reshape(x, K.stack([x_size, 1, dim])), K.stack([1, y_size, 1]))
    tiled_y = K.tile(K.reshape(y, K.stack([1, y_size, dim])), K.stack([x_size, 1, 1]))
    return K.exp(
        -tf.reduce_mean(K.square(tiled_x - tiled_y), axis=2) / K.cast(dim, tf.float32)
    )


def compute_mmd(x, y):
    x_kernel = compute_kernel(x, x)
    y_kernel = compute_kernel(y, y)
    xy_kernel = compute_kernel(x, y)
    return (
        tf.reduce_mean(x_kernel)
        + tf.reduce_mean(y_kernel)
        - 2 * tf.reduce_mean(xy_kernel)
    )


# Custom layers for efficiency/losses
class DenseTranspose(Layer):
    def __init__(self, dense, output_dim, activation=None, **kwargs):
        self.dense = dense
        self.output_dim = output_dim
        self.activation = tf.keras.activations.get(activation)
        super().__init__(**kwargs)

    def get_config(self):
        config = super().get_config().copy()
        config.update(
            {
                "dense": self.dense,
                "output_dim": self.output_dim,
                "activation": self.activation,
            }
        )
        return config

    def build(self, batch_input_shape):
        self.biases = self.add_weight(
            name="bias", shape=[self.dense.input_shape[-1]], initializer="zeros"
        )
        super().build(batch_input_shape)

    def call(self, inputs, **kwargs):
        z = tf.matmul(inputs, self.dense.weights[0], transpose_b=True)
        return self.activation(z + self.biases)

    def compute_output_shape(self, input_shape):
        return input_shape[0], self.output_dim


class UncorrelatedFeaturesConstraint(Constraint):
    def __init__(self, encoding_dim, weightage=1.0):
        self.encoding_dim = encoding_dim
        self.weightage = weightage

    def get_config(self):

        config = super().get_config().copy()
        config.update(
            {"encoding_dim": self.encoding_dim, "weightage": self.weightage,}
        )
        return config

    def get_covariance(self, x):
        x_centered_list = []

        for i in range(self.encoding_dim):
            x_centered_list.append(x[:, i] - K.mean(x[:, i]))

        x_centered = tf.stack(x_centered_list)
        covariance = K.dot(x_centered, K.transpose(x_centered)) / tf.cast(
            x_centered.get_shape()[0], tf.float32
        )

        return covariance

    # Constraint penalty
    def uncorrelated_feature(self, x):
        if self.encoding_dim <= 1:
            return 0.0
        else:
            output = K.sum(
                K.square(
                    self.covariance
                    - tf.math.multiply(self.covariance, K.eye(self.encoding_dim))
                )
            )
            return output

    def __call__(self, x):
        self.covariance = self.get_covariance(x)
        return self.weightage * self.uncorrelated_feature(x)


class MultivariateNormalDiag(tfpl.DistributionLambda):
    def __init__(
        self,
        event_size,
        convert_to_tensor_fn=tfd.Distribution.sample,
        validate_args=True,
        **kwargs
    ):

        super(MultivariateNormalDiag, self).__init__(
            lambda t: MultivariateNormalDiag.new(t, event_size, validate_args),
            convert_to_tensor_fn,
            **kwargs
        )

    @staticmethod
    def new(params, event_size, epsilon=1e-8, validate_args=False, name=None):
        """Create the distribution instance from a `params` vector."""
        with tf.name_scope(name or "MultivariateNormalDiag"):
            params = tf.convert_to_tensor(params, name="params")
        return tfd.mvn_diag.MultivariateNormalDiag(
            loc=params[..., :event_size],
            scale_diag=params[..., event_size:]+epsilon,
            validate_args=validate_args,
        )

    @staticmethod
    def params_size(event_size, name=None):
        """The number of `params` needed to create a single distribution."""
        with tf.name_scope(name or "MultivariateNormalDiag_params_size"):
            return 2 * event_size


class KLDivergenceLayer(tfpl.KLDivergenceAddLoss):
    def __init__(self, *args, **kwargs):
        self.is_placeholder = True
        super(KLDivergenceLayer, self).__init__(*args, **kwargs)

    def call(self, distribution_a):
        kl_batch = self._regularizer(distribution_a)
        self.add_loss(kl_batch, inputs=[distribution_a])
        self.add_metric(
            kl_batch, aggregation="mean", name="kl_divergence",
        )
        self.add_metric(self._regularizer._weight, aggregation="mean", name="kl_rate")

        return distribution_a


class MMDiscrepancyLayer(Layer):
    """ Identity transform layer that adds MM discrepancy
    to the final model loss.
    """

    def __init__(self, prior, beta=1.0, *args, **kwargs):
        self.is_placeholder = True
        self.beta = beta
        self.prior = prior
        super(MMDiscrepancyLayer, self).__init__(*args, **kwargs)

    def get_config(self):
        config = super().get_config().copy()
        config.update({"beta": self.beta})
        config.update({"prior": self.prior})
        return config

    def call(self, z, **kwargs):
        true_samples = self.prior.sample(1)
        mmd_batch = self.beta * compute_mmd(true_samples, z)
        self.add_loss(K.mean(mmd_batch), inputs=z)
        self.add_metric(mmd_batch, aggregation="mean", name="mmd")
        self.add_metric(self.beta, aggregation="mean", name="mmd_rate")

        return z
